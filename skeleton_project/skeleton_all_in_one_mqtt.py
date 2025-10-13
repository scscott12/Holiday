#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Holiday Skeleton — ONE-FILE service (HA-ready)
See INSTALL.txt for setup and README.md for overview.
"""
import os, sys, time, json, queue, subprocess, collections, random, threading, re, wave, signal
from datetime import datetime
import numpy as np

def _safe_im(name, sub=None):
    try:
        return __import__(name) if sub is None else __import__(name, fromlist=[sub])
    except Exception as e:
        print(f"[import] {name} failed: {e}")
        return None

sd          = _safe_im("sounddevice")
requests    = _safe_im("requests")
vosk        = _safe_im("vosk")
gpiozero    = _safe_im("gpiozero")
board       = _safe_im("board")
busio       = _safe_im("busio")
pca9685_mod = _safe_im("adafruit_pca9685")
try:
    import adafruit_motor.servo as ada_servo_mod
except Exception as e:
    print("[import] adafruit_motor.servo failed:", e)
    ada_servo_mod = None
paho = _safe_im("paho") or _safe_im("paho.mqtt.client")

def clamp(x, lo, hi):
    try: 
        return lo if x < lo else hi if x > hi else x
    except: 
        return lo

def envs(k, d): v=os.getenv(k); return v if v is not None else d

HOME         = envs("HOME", "/home/pi")
DEVICE_NAME  = envs("DEVICE_NAME", "skeleton")
MQTT_HOST    = envs("MQTT_HOST", "<ipAddress>")
MQTT_PORT    = int(envs("MQTT_PORT", "1883"))
MQTT_USER    = envs("MQTT_USER", "<Username>")
MQTT_PASS    = envs("MQTT_PASS", "")
MQTT_BASE    = f"holiday/{DEVICE_NAME}"

MODEL_PATH   = envs("MODEL_PATH", f"{HOME}/models/vosk-en")
PIPER_BIN    = envs("PIPER_BIN",  f"{HOME}/bin/piper/piper")
PIPER_MODEL  = envs("PIPER_MODEL",f"{HOME}/piper/en-gb-alan-low.onnx")
TTS_WAV      = envs("TTS_WAV", "/tmp/tts.wav")

PCA_FREQ     = int(envs("PCA_FREQ","50"))
JAW_CH       = int(envs("JAW_CH","0"))
EYES_CH      = int(envs("EYES_CH","4"))
JAW_MIN_US   = int(envs("JAW_MIN_US","512"))
JAW_MAX_US   = int(envs("JAW_MAX_US","1000"))
JAW_REST_FRAC= float(envs("JAW_REST_FRAC","0.25"))
JAW_MAX_FRAC = float(envs("JAW_MAX_FRAC","1.0"))

EYES_IDLE_FRAC   = float(envs("EYES_IDLE_FRAC","0.0"))
EYES_LISTEN_FRAC = float(envs("EYES_LISTEN_FRAC","0.18"))
EYES_SPEAK_FRAC  = float(envs("EYES_SPEAK_FRAC","1.0"))
EYES_INVERT      = int(envs("EYES_INVERT","0"))

PIR_PIN          = int(envs("PIR_PIN","17"))
MOTION_HOLD_SEC  = float(envs("MOTION_HOLD_SEC","0.8"))
MOTION_COOLDOWN_SEC = float(envs("MOTION_COOLDOWN_SEC","8.0"))

VOSK_RATE        = int(envs("VOSK_RATE","16000"))
SD_BLOCKSIZE     = int(envs("SD_BLOCKSIZE","1024"))
ENERGY_GATE      = float(envs("ENERGY_GATE","180"))
MIN_TEXT_LEN     = int(envs("MIN_TEXT_LEN","2"))
PREROLL_SEC      = float(envs("PREROLL_SEC","0.5"))
NO_SPEECH_TIMEOUT= float(envs("NO_SPEECH_TIMEOUT","10.0"))

OLLAMA_URL   = envs("OLLAMA_URL","http://127.0.0.1:11434/api/generate")
OLLAMA_MODEL = envs("OLLAMA_MODEL","qwen2.5:0.5b")
KEEP_ALIVE   = envs("KEEP_ALIVE","24h")
OLLAMA_TIMEOUT = (3, 30)
OLLAMA_OPTS  = {"num_predict": 50, "num_thread": 4, "temperature": 0.6, "repeat_penalty": 1.05, "num_ctx": 128}
VOLUME       = float(envs("VOLUME","1.0"))

SYSTEM_PROMPT = (
    "You are a semi-retired pirate trying to act normal in modern times. "
    "Be witty, chaotic, and playfully dramatic — a mix of trauma dumping and humor. "
    "Use Gen Z slang naturally. One short sentence. Never curse."
)
PROMPTS_PATH = envs("PROMPTS_PATH", os.path.join(os.path.dirname(__file__), "prompts.json"))

MORNING_LINES=[
"Ayo, morning hit harder than a cannonball — you sleep or just vibe through it?",
"Lowkey forgot the sun still does this rising thing — you a morning person or nah?",
"Bruh, I’m not even caffeinated and the sky’s already loud. You good?"
]
AFTERNOON_LINES=[
"Midday vibes hittin’ weird — feels like the ocean’s plotting something again.",
"Lowkey feel like I should be plundering something right now. You busy?",
"Afternoons feel like filler episodes — you got main quest energy left?"
]
EVENING_LINES=[
"Yo, sun’s clocking out — wish I could too, fr.",
"Twilight’s giving ‘emotional damage but pretty’ — you good?",
"The light hits different — makes me wanna overshare then disappear."
]
NIGHT_LINES=[
"Moon’s out and I’m spiraling — you up?",
"Quiet seas are sus — chaos is comforting, no cap.",
"Night’s got that ‘haunted by choices but vibin’ anyway’ energy."
]
GOODBYE_LINES=[
"Aight, gotta bounce — my emotional support parrot’s acting toxic again.",
"Fr tho, storms don’t wait for sad boys. Catch you on calmer seas.",
"Peace out, sailor — my attention span just walked the plank."
]

def pick_opening_line()->str:
    hr=datetime.now().hour
    if 5<=hr<12: return random.choice(MORNING_LINES)
    if 12<=hr<17: return random.choice(AFTERNOON_LINES)
    if 17<=hr<21: return random.choice(EVENING_LINES)
    return random.choice(NIGHT_LINES)

def _try_load_prompts():
    global SYSTEM_PROMPT,MORNING_LINES,AFTERNOON_LINES,EVENING_LINES,NIGHT_LINES,GOODBYE_LINES
    try:
        if os.path.isfile(PROMPTS_PATH):
            with open(PROMPTS_PATH,"r",encoding="utf-8") as f: data=json.load(f)
            SYSTEM_PROMPT=data.get("SYSTEM_PROMPT",SYSTEM_PROMPT)
            MORNING_LINES=data.get("MORNING_LINES",MORNING_LINES)
            AFTERNOON_LINES=data.get("AFTERNOON_LINES",AFTERNOON_LINES)
            EVENING_LINES=data.get("EVENING_LINES",EVENING_LINES)
            NIGHT_LINES=data.get("NIGHT_LINES",NIGHT_LINES)
            GOODBYE_LINES=data.get("GOODBYE_LINES",GOODBYE_LINES)
            print(f"[prompts] Loaded from {PROMPTS_PATH}")
    except Exception as e: print("[prompts] Failed:",e)
_try_load_prompts()

EXIT_RE=re.compile(r"\b(good\s?bye|bye|farewell|that'?s all|we('re| are) done|stop now|quit|exit|shut\s?down)\b",re.I)

def _make_mqtt_client():
    try:
        from paho.mqtt import client as mqtt_client
        client_id=f"holiday_{DEVICE_NAME}"
        try: return mqtt_client.Client(client_id=client_id, clean_session=True, transport="tcp")
        except TypeError: return mqtt_client.Client(client_id)
    except Exception as e: print("[mqtt] client unavailable:",e); return None

mqttc=_make_mqtt_client()
_mqtt_connected=False
def mqtt_pub(topic,payload,retain=False):
    try:
        if mqttc: mqttc.publish(f"{MQTT_BASE}/{topic}",payload,retain=retain)
    except Exception as e: print("[mqtt publish]",e)
def mqtt_pub_abs(topic,payload,retain=False):
    try:
        if mqttc: mqttc.publish(topic,payload,retain=retain)
    except Exception: pass
    mqtt_pub(topic,payload,retain)

def _on_message(client,userdata,msg):
    t=msg.topic; p=msg.payload.decode("utf-8","ignore").strip()
    if t.endswith("/say/set"):
        _transcript_add("assistant",p); threading.Thread(target=lambda:speak_with_jaw(p),daemon=True).start(); return
    if t.endswith("/motion/trigger/set"): _manual_trigger(); return
    if t.endswith("/eyes/dim/set"):
        try: v=float(p); v=v/100.0 if v>1.0 else v; _set_eyes_dim(v)
        except: pass; return
    if t.endswith("/eyes/full/set"):
        try: v=float(p); v=v/100.0 if v>1.0 else v; _set_eyes_full(v)
        except: pass; return
    if t.endswith("/blink/set"): eyes_blink(6,120); return
    if t.endswith("/flicker/set"): eyes_flicker(5.0,0.2,0.7,60); return
    if t.endswith("/volume/set"):
        try: v=float(p); v=v/100.0 if v>2.0 else v; _set_volume(v)
        except: pass; return
    if t.endswith("/motion/enabled/set"): _set_motion_enabled(p); return
    if t.endswith("/night_mode/set"): _toggle_night_mode(p); return
    if t.endswith("/restart/set"): mqtt_pub("availability","offline",retain=True); time.sleep(0.2); os._exit(0)

def _on_connect(client,userdata,flags,rc,properties=None):
    global _mqtt_connected; _mqtt_connected=(rc==0)
    print(f"[mqtt] on_connect rc={rc}")
    if _mqtt_connected:
        mqtt_pub("availability","online",retain=True); mqtt_pub("status","idle",retain=True)
        publish_mqtt_discovery()
        subs=["say/set","eyes/dim/set","eyes/full/set","blink/set","flicker/set","volume/set",
              "motion/trigger/set","motion/enabled/set","night_mode/set","restart/set"]
        for path in subs:
            try: client.subscribe(f"{MQTT_BASE}/{path}")
            except Exception as e: print("[mqtt subscribe]",e)
        mqtt_pub("ready","ON"); eyes_off()

def _on_disconnect(client,userdata,rc,properties=None):
    global _mqtt_connected; _mqtt_connected=False
    print(f"[mqtt] on_disconnect rc={rc}")

def mqtt_connect():
    if mqttc is None: return
    try:
        mqttc.on_connect=_on_connect; mqttc.on_disconnect=_on_disconnect; mqttc.on_message=_on_message
        try: mqttc.will_set(f"{MQTT_BASE}/availability","offline",retain=True)
        except Exception: pass
        if MQTT_USER:
            try: mqttc.username_pw_set(MQTT_USER,MQTT_PASS)
            except Exception: pass
        mqttc.connect(MQTT_HOST,MQTT_PORT,keepalive=60); mqttc.loop_start()
        for _ in range(30): time.sleep(0.05)
    except Exception as e: print("[mqtt connect]",e)

_pca=None; _eyes_ch=None; _jaw=None
_eyes_effect_thread=None; _eyes_effect_stop=threading.Event(); _eyes_lock=threading.Lock()

def eyes_set(frac:float):
    frac=clamp(float(frac),0.0,1.0)
    if EYES_INVERT: frac=1.0-frac
    with _eyes_lock:
        if _eyes_ch is not None:
            try: _eyes_ch.duty_cycle=int(0xFFFF*frac)
            except Exception as e: print("[eyes set]",e)

def _stop_eyes_effect():
    global _eyes_effect_thread,_eyes_effect_stop
    _eyes_effect_stop.set()
    if _eyes_effect_thread and _eyes_effect_thread.is_alive():
        try: _eyes_effect_thread.join(timeout=0.5)
        except: pass
    _eyes_effect_stop=threading.Event(); _eyes_effect_thread=None

def eyes_off():    _stop_eyes_effect(); eyes_set(0.0)
def eyes_idle():   _stop_eyes_effect(); eyes_set(EYES_IDLE_FRAC)
def eyes_listen(): _stop_eyes_effect(); eyes_set(EYES_LISTEN_FRAC)
def eyes_speak():  _stop_eyes_effect(); eyes_set(EYES_SPEAK_FRAC)

def eyes_blink(count=6, period_ms=120, low=0.0, high=None):
    global _eyes_effect_thread,_eyes_effect_stop
    _stop_eyes_effect(); high = EYES_SPEAK_FRAC if high is None else clamp(high,0,1)
    def run():
        for _ in range(max(1,int(count))):
            if _eyes_effect_stop.is_set(): break
            eyes_set(high); time.sleep(max(0.01, period_ms/1000.0/2))
            eyes_set(low);  time.sleep(max(0.01, period_ms/1000.0/2))
        eyes_idle()
    _eyes_effect_thread=threading.Thread(target=run,daemon=True); _eyes_effect_thread.start()

def eyes_flicker(duration_s=5.0, base=0.2, span=0.7, step_ms=60):
    global _eyes_effect_thread,_eyes_effect_stop
    _stop_eyes_effect(); base=clamp(base,0,1); span=clamp(span,0,1-base); start=time.time()
    def run():
        while time.time()-start<duration_s and not _eyes_effect_stop.is_set():
            eyes_set(base+random.random()*span); time.sleep(max(0.02, step_ms/1000.0))
        eyes_idle()
    _eyes_effect_thread=threading.Thread(target=run,daemon=True); _eyes_effect_thread.start()

def _jaw_set(frac:float):
    try:
        if _jaw is not None: _jaw.fraction=clamp(frac,0,1)
    except Exception as e: print("[jaw]",e)

def jaw_env_from_wav(path:str):
    try:
        wf=wave.open(path,"rb"); rate=wf.getframerate(); nchan=wf.getnchannels(); hop=int(rate*0.02)
        env=[]
        while True:
            frames=wf.readframes(hop)
            if not frames: break
            data=np.frombuffer(frames,dtype=np.int16)
            if nchan>1: data=data.reshape(-1,nchan)[:,0]
            rms=float(np.sqrt(np.mean(data.astype(np.float32)**2))) if data.size else 0.0
            env.append(rms)
        wf.close()
        if not env: env=[0.0]
        arr=np.array(env,dtype=np.float32); m=float(np.percentile(arr,95) or 1.0)
        return np.clip(arr/m,0.0,1.0)
    except Exception as e:
        print("[jaw env]",e); return np.array([0.0],dtype=np.float32)

def jaw_drive_by_env(env:np.ndarray, rest=JAW_REST_FRAC, mx=JAW_MAX_FRAC, period=0.02):
    for v in env: _jaw_set(rest+(mx-rest)*float(v)); time.sleep(period)
    _jaw_set(rest)

def jaw_chatter_fallback(text:str):
    dur=clamp(1.1+0.05*len(text),1.0,6.0); end=time.time()+dur
    rest,open_=JAW_REST_FRAC,JAW_MAX_FRAC
    while time.time()<end:
        _jaw_set(open_); time.sleep(0.09); _jaw_set(rest); time.sleep(0.07)
    _jaw_set(rest)

def _amplify_wav_inplace(path:str, volume:float):
    try:
        volume=float(volume); volume=(volume/100.0) if volume>2.0 else volume; volume=clamp(volume,0,2)
        if abs(volume-1.0)<1e-3: return
        with wave.open(path,"rb") as wf: params=wf.getparams(); frames=wf.readframes(wf.getnframes())
        data=np.frombuffer(frames,dtype=np.int16).astype(np.float32)
        data=np.clip(np.round(data*volume),-32768,32767).astype(np.int16)
        with wave.open(path,"wb") as wf2: wf2.setparams(params); wf2.writeframes(data.tobytes())
    except Exception as e: print("[volume]",e)

def speak_wav_play(path:str):
    try: subprocess.run(["aplay","-q",path],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    except Exception as e: print("[aplay]",e)

def speak_with_jaw(text:str):
    if not text: return
    try:
        p=subprocess.Popen([PIPER_BIN,"-m",PIPER_MODEL,"-f",TTS_WAV,"-q"],stdin=subprocess.PIPE,text=True,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        p.communicate(input=text)
    except Exception as e:
        print("[TTS]",e); eyes_speak(); jaw_chatter_fallback(text); eyes_off(); return
    _amplify_wav_inplace(TTS_WAV,VOLUME)
    env=jaw_env_from_wav(TTS_WAV)
    mqtt_pub("speaking","ON"); eyes_speak()
    t=threading.Thread(target=speak_wav_play,args=(TTS_WAV,),daemon=True); t.start()
    (jaw_chatter_fallback(text) if float(np.max(env) if env.size else 0.0)<0.05 else jaw_drive_by_env(env))
    t.join(); mqtt_pub("speaking","OFF"); eyes_listen()

def llm_reply(user_text:str)->str:
    payload={"model":OLLAMA_MODEL,"prompt":user_text,"system":SYSTEM_PROMPT,
             "stream":False,"keep_alive":KEEP_ALIVE,"options":OLLAMA_OPTS}
    try:
        start=time.monotonic()
        r=requests.post(OLLAMA_URL,json=payload,timeout=(3,30)); r.raise_for_status()
        elapsed=time.monotonic()-start; txt=(r.json().get("response") or "").strip().split("\n")[0].strip()
        mqtt_pub("llm/reply_time",f"{elapsed:.2f}")
        return txt or "Arrr, me brain’s foggy — try that again."
    except Exception as e:
        print("[LLM]",e); return "Arrr, I be old and forgetful — say it again, matey!"

stt_enabled=True; _VOSK_MODEL=None
if vosk is None:
    stt_enabled=False; print("[stt] disabled (vosk not available)")
else:
    try:
        if not os.path.isdir(MODEL_PATH): raise RuntimeError(f"model path missing: {MODEL_PATH}")
        _VOSK_MODEL=vosk.Model(MODEL_PATH)
    except Exception as e:
        stt_enabled=False; print(f"[stt] disabled: {e}")

def pick_input_device():
    if sd is None: return None,"no-sounddevice"
    try:
        for idx,d in enumerate(sd.query_devices()):
            if d.get("max_input_channels",0)>0: return idx,d.get("name","mic")
    except Exception as e: print("[audio]",e)
    return None,"none"
in_idx,in_name=pick_input_device()
if in_idx is None: stt_enabled=False; print("[audio] listening disabled (no input device)")
else: print(f"Mic selected: {in_name}")

def resample_linear_int16(chunk_i16:np.ndarray,src_hz:int,dst_hz:int,state:dict)->np.ndarray:
    if src_hz==dst_hz or chunk_i16.size==0: return chunk_i16
    prev=state.get("prev",np.zeros(0,np.int16)); x=np.concatenate([prev,chunk_i16])
    if x.size<2: state["prev"]=x; return np.zeros(0,np.int16)
    ratio=dst_hz/float(src_hz); phase=state.get("phase",0.0)
    out_len=int(np.floor((len(x)-1-phase)*ratio))
    if out_len<=0: state["prev"]=x; state["phase"]=phase; return np.zeros(0,np.int16)
    idx=phase+np.arange(out_len)/ratio
    i0=np.floor(idx).astype(np.int32); i1=np.clip(i0+1,0,len(x)-1)
    frac=(idx-i0).astype(np.float32)
    y=x[i0].astype(np.float32)*(1.0-frac)+x[i1].astype(np.float32)*frac
    y=np.clip(np.round(y),-32768,32767).astype(np.int16)
    state["prev"]=x[i0[-1]+1:]; state["phase"]=idx[-1]-i0[-1]; return y

def record_once(input_index:int,capture_rate:int,timeout_s:float)->str:
    if not stt_enabled or sd is None or _VOSK_MODEL is None: return ""
    rec=vosk.KaldiRecognizer(_VOSK_MODEL,VOSK_RATE); rec.SetWords(True)
    q=queue.Queue(maxsize=128); rs_state={"prev":np.zeros(0,np.int16),"phase":0.0}
    preroll=collections.deque(maxlen=int(VOSK_RATE*PREROLL_SEC))
    speaking=False; voiced_frames=0; MIN_VOICED_FRAMES=8
    deadline=time.monotonic()+timeout_s
    def cb(indata,frames,time_info,status):
        try: q.put_nowait(bytes(indata))
        except queue.Full: pass
    with sd.RawInputStream(samplerate=capture_rate,blocksize=SD_BLOCKSIZE,device=input_index,dtype="int16",channels=1,callback=cb):
        while time.monotonic()<deadline:
            try: data=q.get(timeout=0.25)
            except queue.Empty: continue
            chunk=np.frombuffer(data,dtype=np.int16)
            if float(np.mean(np.abs(chunk)))>ENERGY_GATE:
                voiced_frames=min(voiced_frames+1,MIN_VOICED_FRAMES+10)
                if not speaking and len(preroll)>0 and voiced_frames>=MIN_VOICED_FRAMES:
                    rec.AcceptWaveform(np.array(preroll,np.int16).tobytes()); speaking=True
            else:
                if voiced_frames>0: voiced_frames-=1
            rs=resample_linear_int16(chunk,capture_rate,VOSK_RATE,rs_state)
            if rs.size:
                preroll.extend(rs.tolist())
                if rec.AcceptWaveform(rs.tobytes()):
                    j=json.loads(rec.Result()); txt=(j.get("text") or "").strip()
                    if txt and len(txt.split())>=MIN_TEXT_LEN: return txt
    j=json.loads(rec.FinalResult()); txt=(j.get("text") or "").strip()
    if txt and len(txt.split())>=MIN_TEXT_LEN: return txt
    return ""

motion_enabled=True; motion_count=0
class _DummyPIR:
    motion_detected=False
    def wait_for_motion(self): time.sleep(0.25)
pir=None
if gpiozero is not None:
    try:
        pir=gpiozero.MotionSensor(PIR_PIN,queue_len=5,sample_rate=25,threshold=0.5)
        def _pir_on():
            global motion_count; motion_count+=1
            mqtt_pub("motion","ON"); mqtt_pub("motion/count",str(motion_count))
        def _pir_off():
            mqtt_pub("motion","OFF"); eyes_off()
        pir.when_motion=_pir_on; pir.when_no_motion=_pir_off; print("PIR ready")
    except Exception as e: print("[pir]",e)
if pir is None:
    pir=_DummyPIR(); print("[pir] disabled; using dummy")

if busio and pca9685_mod and ada_servo_mod and board:
    try:
        i2c=busio.I2C(board.SCL,board.SDA)
        _pca=pca9685_mod.PCA9685(i2c); _pca.frequency=PCA_FREQ
        _eyes_ch=_pca.channels[EYES_CH]
        _jaw=ada_servo_mod.Servo(_pca.channels[JAW_CH],min_pulse=JAW_MIN_US,max_pulse=JAW_MAX_US)
    except Exception as e:
        print("[PCA]",e); _pca=None; _eyes_ch=None; _jaw=None
else:
    print("[PCA] libs unavailable; eyes/jaw disabled")

def eyes_safe_off_startup():
    try:
        if _eyes_ch is not None:
            _eyes_ch.duty_cycle=int(0xFFFF*(0.0 if not EYES_INVERT else 1.0))
    except Exception as e: print("[eyes startup off]",e)
eyes_safe_off_startup()
if '_jaw' in globals() and _jaw:
    try: _jaw.fraction=JAW_REST_FRAC
    except Exception as e: print("[jaw init]",e)

def _ha_device():
    return {"identifiers":[f"holiday_{DEVICE_NAME}"],"name":DEVICE_NAME.capitalize(),
            "manufacturer":"SkeletonWorks","model":"Animatronic v1"}

def _disc_publish(path,payload):
    mqtt_pub_abs(f"homeassistant/{path}",json.dumps(payload),retain=True)

def publish_mqtt_discovery():
    dev=_ha_device(); base=MQTT_BASE
    _disc_publish(f"binary_sensor/{DEVICE_NAME}/motion/config",{
        "name":f"{DEVICE_NAME.capitalize()} Motion","uniq_id":f"holiday_{DEVICE_NAME}_motion",
        "stat_t":f"{base}/motion","pl_on":"ON","pl_off":"OFF","avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"binary_sensor/{DEVICE_NAME}/speaking/config",{
        "name":f"{DEVICE_NAME.capitalize()} Speaking","uniq_id":f"holiday_{DEVICE_NAME}_speaking",
        "stat_t":f"{base}/speaking","pl_on":"ON","pl_off":"OFF","avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"binary_sensor/{DEVICE_NAME}/ready/config",{
        "name":f"{DEVICE_NAME.capitalize()} Ready","uniq_id":f"holiday_{DEVICE_NAME}_ready",
        "stat_t":f"{base}/ready","pl_on":"ON","pl_off":"OFF","avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"sensor/{DEVICE_NAME}/status/config",{
        "name":f"{DEVICE_NAME.capitalize()} Status","uniq_id":f"holiday_{DEVICE_NAME}_status",
        "stat_t":f"{base}/status","avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"sensor/{DEVICE_NAME}/reply_time/config",{
        "name":f"{DEVICE_NAME.capitalize()} Reply Time","uniq_id":f"holiday_{DEVICE_NAME}_reply_time",
        "stat_t":f"{base}/llm/reply_time","unit_of_measurement":"s","avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"sensor/{DEVICE_NAME}/transcript/config",{
        "name":f"{DEVICE_NAME.capitalize()} Transcript","uniq_id":f"holiday_{DEVICE_NAME}_transcript",
        "stat_t":f"{base}/transcript","avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"number/{DEVICE_NAME}/eyes_dim/config",{
        "name":f"{DEVICE_NAME.capitalize()} Eyes Dim %","uniq_id":f"holiday_{DEVICE_NAME}_eyes_dim",
        "cmd_t":f"{base}/eyes/dim/set","stat_t":f"{base}/eyes/dim","min":0,"max":100,"step":1,"mode":"box",
        "unit_of_measurement":"%","avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"number/{DEVICE_NAME}/eyes_full/config",{
        "name":f"{DEVICE_NAME.capitalize()} Eyes Full %","uniq_id":f"holiday_{DEVICE_NAME}_eyes_full",
        "cmd_t":f"{base}/eyes/full/set","stat_t":f"{base}/eyes/full","min":0,"max":100,"step":1,"mode":"box",
        "unit_of_measurement":"%","avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"number/{DEVICE_NAME}/volume/config",{
        "name":f"{DEVICE_NAME.capitalize()} Volume %","uniq_id":f"holiday_{DEVICE_NAME}_volume",
        "cmd_t":f"{base}/volume/set","stat_t":f"{base}/volume","min":0,"max":200,"step":5,"mode":"box",
        "unit_of_measurement":"%","avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"switch/{DEVICE_NAME}/motion_enabled/config",{
        "name":f"{DEVICE_NAME.capitalize()} Motion Enabled","uniq_id":f"holiday_{DEVICE_NAME}_motion_enabled",
        "cmd_t":f"{base}/motion/enabled/set","stat_t":f"{base}/motion/enabled","pl_on":"ON","pl_off":"OFF",
        "avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"switch/{DEVICE_NAME}/night_mode/config",{
        "name":f"{DEVICE_NAME.capitalize()} Night Mode","uniq_id":f"holiday_{DEVICE_NAME}_night_mode",
        "cmd_t":f"{base}/night_mode/set","stat_t":f"{base}/night_mode","pl_on":"ON","pl_off":"OFF",
        "avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"button/{DEVICE_NAME}/say/config",{
        "name":f"{DEVICE_NAME.capitalize()} Say","uniq_id":f"holiday_{DEVICE_NAME}_say_btn",
        "cmd_t":f"{base}/say/set","avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"button/{DEVICE_NAME}/blink/config",{
        "name":f"{DEVICE_NAME.capitalize()} Blink","uniq_id":f"holiday_{DEVICE_NAME}_blink_btn",
        "cmd_t":f"{base}/blink/set","avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"button/{DEVICE_NAME}/flicker/config",{
        "name":f"{DEVICE_NAME.capitalize()} Flicker","uniq_id":f"holiday_{DEVICE_NAME}_flicker_btn",
        "cmd_t":f"{base}/flicker/set","avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"button/{DEVICE_NAME}/restart/config",{
        "name":f"{DEVICE_NAME.capitalize()} Restart Service","uniq_id":f"holiday_{DEVICE_NAME}_restart_btn",
        "cmd_t":f"{base}/restart/set","avty_t":f"{base}/availability","dev":dev})
    _disc_publish(f"button/{DEVICE_NAME}/motion_trigger/config",{
        "name":f"{DEVICE_NAME.capitalize()} Trigger Motion","uniq_id":f"holiday_{DEVICE_NAME}_motion_trigger_btn",
        "cmd_t":f"{base}/motion/trigger/set","avty_t":f"{base}/availability","dev":dev})

night_mode=False; _day={"listen":None,"speak":None,"vol":None}
def _set_eyes_dim(v):
    global EYES_LISTEN_FRAC; EYES_LISTEN_FRAC=clamp(v,0,1); eyes_listen(); mqtt_pub("eyes/dim",str(int(round(100*EYES_LISTEN_FRAC))))
def _set_eyes_full(v):
    global EYES_SPEAK_FRAC;  EYES_SPEAK_FRAC=clamp(v,0,1); eyes_speak(); mqtt_pub("eyes/full",str(int(round(100*EYES_SPEAK_FRAC))))
def _set_volume(v):
    global VOLUME; VOLUME=clamp(v,0,2); mqtt_pub("volume",str(int(round(100*VOLUME))))
def _set_motion_enabled(payload):
    global motion_enabled; motion_enabled=str(payload).lower() in ("on","true","1","yes")
    mqtt_pub("motion/enabled","ON" if motion_enabled else "OFF")
def _toggle_night_mode(payload):
    global night_mode,EYES_LISTEN_FRAC,EYES_SPEAK_FRAC,VOLUME,_day
    want=str(payload).lower() in ("on","true","1","yes")
    if want and not night_mode:
        _day["listen"],_day["speak"],_day["vol"]=EYES_LISTEN_FRAC,EYES_SPEAK_FRAC,VOLUME
        EYES_LISTEN_FRAC=clamp(EYES_LISTEN_FRAC*0.35,0,1); EYES_SPEAK_FRAC=clamp(EYES_SPEAK_FRAC*0.6,0,1); VOLUME=clamp(VOLUME*0.6,0,2)
        night_mode=True
    elif not want and night_mode:
        if _day["listen"] is not None: EYES_LISTEN_FRAC=_day["listen"]
        if _day["speak"]  is not None: EYES_SPEAK_FRAC=_day["speak"]
        if _day["vol"]    is not None: VOLUME=_day["vol"]
        night_mode=False
    mqtt_pub("night_mode","ON" if night_mode else "OFF"); mqtt_pub("volume",str(int(round(100*VOLUME))))
    mqtt_pub("eyes/dim",str(int(round(100*EYES_LISTEN_FRAC)))); mqtt_pub("eyes/full",str(int(round(100*EYES_SPEAK_FRAC))))

_transcript=None
def _transcript_start():
    global _transcript; _transcript={"startts":time.time(),"utterances":[]}
def _transcript_add(role,text):
    try:
        if _transcript is not None: _transcript["utterances"].append({"t":time.time(),"role":role,"text":text})
    except: pass
def _who(role):
    return {'assistant':'Skeleton','user':'Visitor','command':'Command'}.get(role, role or 'Unknown')
def _transcript_pretty():
    try:
        lines=[]
        for u in (_transcript.get('utterances') or []):
            who=_who(u.get('role')); txt=u.get('text','')
            lines.append(f'**{who}**: "{txt}"')
        return "\n".join(lines)
    except Exception: return ""
def _transcript_publish_and_clear():
    global _transcript
    try:
        if _transcript:
            _transcript["endts"]=time.time()
            pretty=_transcript_pretty()
            if pretty: mqtt_pub("transcript",pretty)
    except Exception as e: print("[transcript]",e)
    _transcript=None

def on_sigint(sig,frame):
    print("Exiting...")
    try: mqtt_pub("availability","offline",retain=True)
    except: pass
    try: eyes_off()
    except: pass
    try:
        if '_pca' in globals() and _pca: _pca.deinit()
    except: pass
    try:
        if mqttc: mqttc.loop_stop(); mqttc.disconnect()
    except: pass
    sys.exit(0)
signal.signal(signal.SIGINT,on_sigint)

def _do_mqtt_connect():
    if mqttc is not None:
        mqtt_connect()
        mqtt_pub("ready","ON"); eyes_off()
_do_mqtt_connect()

try:
    if requests:
        requests.post(OLLAMA_URL,json={"model":OLLAMA_MODEL,"prompt":"warming","stream":False,
                                       "keep_alive":KEEP_ALIVE,"options":{"num_predict":1}},timeout=(2,10))
except Exception: pass

def _conversation_loop():
    while True:
        eyes_listen()
        text=record_once(in_idx or 0,44100,NO_SPEECH_TIMEOUT)
        if not text:
            gb=random.choice(GOODBYE_LINES) if GOODBYE_LINES else "Goodbye."
            _transcript_add("assistant",gb); speak_with_jaw(gb); _transcript_publish_and_clear(); eyes_off(); break
        if EXIT_RE.search(text):
            _transcript_add("user",text)
            gb=random.choice(GOODBYE_LINES) if GOODBYE_LINES else "Goodbye."
            _transcript_add("assistant",gb); speak_with_jaw(gb); _transcript_publish_and_clear(); eyes_off(); break
        _transcript_add("user",text)
        eyes_listen()
        reply=llm_reply(text)
        _transcript_add("assistant",reply)
        speak_with_jaw(reply)
        eyes_listen()

_last_talk=0.0
def _manual_trigger():
    global _last_talk
    now=time.time()
    if (now-_last_talk)<MOTION_COOLDOWN_SEC: return
    _last_talk=now
    _transcript_start()
    opener=pick_opening_line()
    _transcript_add("assistant",opener); speak_with_jaw(opener)
    _conversation_loop()

print("👀 Waiting for motion…")
while True:
    try:
        eyes_off()
        pir.wait_for_motion()
        if not motion_enabled: continue
        start=time.time()
        while getattr(pir,"motion_detected",False) and (time.time()-start)<MOTION_HOLD_SEC:
            time.sleep(0.05)
        if not (getattr(pir,"motion_detected",False) and (time.time()-start)>=MOTION_HOLD_SEC):
            continue
        _manual_trigger()
    except KeyboardInterrupt:
        break
    except Exception as e:
        print("[loop]",e); time.sleep(0.5)
