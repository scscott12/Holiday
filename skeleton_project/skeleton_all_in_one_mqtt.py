#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Holiday Skeleton single-process runtime (Home Assistant ready)."""
import os, time, json, queue, subprocess, random, threading, re, wave, signal
from datetime import datetime
import numpy as np

from holiday_skeleton.audio import SpeechGate
from holiday_skeleton.brain import (
    ConversationMemory,
    OllamaStreamingClient,
    normalize_ollama_chat_url,
)
from holiday_skeleton.controller import EventKind, RuntimeState, SkeletonController
from holiday_skeleton.discovery import discovery_messages
from holiday_skeleton.speech import PiperSpeechEngine, SpeechEngineError

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

USER_HOME    = envs("HOME", "/home/pi")
DEVICE_NAME  = envs("DEVICE_NAME", "skeleton")
MQTT_HOST    = envs("MQTT_HOST", "<ipAddress>")
MQTT_PORT    = int(envs("MQTT_PORT", "1883"))
MQTT_USER    = envs("MQTT_USER", "<Username>")
MQTT_PASS    = envs("MQTT_PASS", "")
MQTT_BASE    = f"holiday/{DEVICE_NAME}"

MODEL_PATH   = envs("MODEL_PATH", f"{USER_HOME}/models/vosk-en")
PIPER_BIN    = envs("PIPER_BIN",  f"{USER_HOME}/bin/piper/piper")
PIPER_MODEL  = envs("PIPER_MODEL",f"{USER_HOME}/piper/en-gb-alan-low.onnx")
PIPER_CONFIG = envs("PIPER_CONFIG","").strip() or None
TTS_WAV      = envs("TTS_WAV", "/tmp/tts.wav")
TTS_FRAME_MS = float(envs("TTS_FRAME_MS","20"))
AUDIO_OUTPUT_DEVICE = envs("AUDIO_OUTPUT_DEVICE","").strip() or None

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
SPEECH_START_TIMEOUT = float(envs("SPEECH_START_TIMEOUT",str(NO_SPEECH_TIMEOUT)))
MIN_VOICED_SEC   = float(envs("MIN_VOICED_SEC","0.16"))
END_SILENCE_SEC  = float(envs("END_SILENCE_SEC","0.75"))
MAX_UTTERANCE_SEC= float(envs("MAX_UTTERANCE_SEC","12.0"))

OLLAMA_URL   = normalize_ollama_chat_url(envs("OLLAMA_CHAT_URL",envs("OLLAMA_URL","http://127.0.0.1:11434/api/chat")))
OLLAMA_MODEL = envs("OLLAMA_MODEL","qwen2.5:0.5b")
KEEP_ALIVE   = envs("KEEP_ALIVE","24h")
OLLAMA_TIMEOUT = (3, 30)
LLM_MEMORY_TURNS = max(0,int(envs("LLM_MEMORY_TURNS","3")))
LLM_CONTEXT_TOKENS = max(128,int(envs("LLM_CONTEXT_TOKENS","512")))
OLLAMA_OPTS  = {"num_predict": 50, "num_thread": 4, "temperature": 0.6, "repeat_penalty": 1.05, "num_ctx": LLM_CONTEXT_TOKENS}
LLM_PHRASE_MIN_CHARS = int(envs("LLM_PHRASE_MIN_CHARS","12"))
LLM_PHRASE_SOFT_CHARS = int(envs("LLM_PHRASE_SOFT_CHARS","36"))
LLM_PHRASE_MAX_CHARS = int(envs("LLM_PHRASE_MAX_CHARS","72"))
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
controller=None

def mqtt_pub(topic,payload,retain=False):
    try:
        if mqttc: mqttc.publish(f"{MQTT_BASE}/{topic}",payload,retain=retain)
    except Exception as e: print("[mqtt publish]",e)
def mqtt_pub_abs(topic,payload,retain=False):
    try:
        if mqttc: mqttc.publish(topic,payload,retain=retain)
    except Exception: pass

def _enqueue(kind,payload=None,source="mqtt"):
    if controller is None:
        print(f"[controller] dropped {kind.value}; runtime not ready")
        return False
    accepted=controller.enqueue(kind,payload,source)
    if not accepted:
        print(f"[controller] coalesced or dropped {kind.value} from {source}")
    return accepted

def _on_message(client,userdata,msg):
    t=msg.topic; p=msg.payload.decode("utf-8","ignore").strip()
    if t.endswith("/say/set"):
        if p: _enqueue(EventKind.SAY,p)
        return
    if t.endswith("/motion/trigger/set"): _enqueue(EventKind.TRIGGER,source="mqtt"); return
    if t.endswith("/eyes/dim/set"):
        try: v=float(p); v=v/100.0 if v>1.0 else v; _enqueue(EventKind.SET_EYES_DIM,v)
        except: pass; return
    if t.endswith("/eyes/full/set"):
        try: v=float(p); v=v/100.0 if v>1.0 else v; _enqueue(EventKind.SET_EYES_FULL,v)
        except: pass; return
    if t.endswith("/blink/set"): _enqueue(EventKind.BLINK); return
    if t.endswith("/flicker/set"): _enqueue(EventKind.FLICKER); return
    if t.endswith("/volume/set"):
        try: v=float(p); v=v/100.0 if v>2.0 else v; _enqueue(EventKind.SET_VOLUME,v)
        except: pass; return
    if t.endswith("/motion/enabled/set"): _enqueue(EventKind.SET_MOTION_ENABLED,p); return
    if t.endswith("/night_mode/set"): _enqueue(EventKind.SET_NIGHT_MODE,p); return
    if t.endswith("/restart/set"): _enqueue(EventKind.RESTART); return

def _on_connect(client,userdata,flags,rc,properties=None):
    global _mqtt_connected; _mqtt_connected=(rc==0)
    print(f"[mqtt] on_connect rc={rc}")
    if _mqtt_connected:
        mqtt_pub("availability","online",retain=True); mqtt_pub("status","starting",retain=True)
        publish_mqtt_discovery()
        subs=["say/set","eyes/dim/set","eyes/full/set","blink/set","flicker/set","volume/set",
              "motion/trigger/set","motion/enabled/set","night_mode/set","restart/set"]
        for path in subs:
            try: client.subscribe(f"{MQTT_BASE}/{path}")
            except Exception as e: print("[mqtt subscribe]",e)
        mqtt_pub("ready","ON")

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

def eyes_blink(count=6, period_ms=120, low=0.0, high=None, blocking=False):
    global _eyes_effect_thread,_eyes_effect_stop
    _stop_eyes_effect(); high = EYES_SPEAK_FRAC if high is None else clamp(high,0,1)
    def run():
        for _ in range(max(1,int(count))):
            if _eyes_effect_stop.is_set(): break
            eyes_set(high); time.sleep(max(0.01, period_ms/1000.0/2))
            eyes_set(low);  time.sleep(max(0.01, period_ms/1000.0/2))
        eyes_set(EYES_IDLE_FRAC)
    _eyes_effect_thread=threading.Thread(target=run,daemon=True); _eyes_effect_thread.start()
    if blocking: _eyes_effect_thread.join()

def eyes_flicker(duration_s=5.0, base=0.2, span=0.7, step_ms=60, blocking=False):
    global _eyes_effect_thread,_eyes_effect_stop
    _stop_eyes_effect(); base=clamp(base,0,1); span=clamp(span,0,1-base); start=time.time()
    def run():
        while time.time()-start<duration_s and not _eyes_effect_stop.is_set():
            eyes_set(base+random.random()*span); time.sleep(max(0.02, step_ms/1000.0))
        eyes_set(EYES_IDLE_FRAC)
    _eyes_effect_thread=threading.Thread(target=run,daemon=True); _eyes_effect_thread.start()
    if blocking: _eyes_effect_thread.join()

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

_speech_lock=threading.Lock()
_speech_engine=None
_llm_client=None

def _legacy_speak_with_jaw(text:str):
    """Compatibility path for installs that have not added piper-tts yet."""
    try:
        p=subprocess.Popen([PIPER_BIN,"-m",PIPER_MODEL,"-f",TTS_WAV,"-q"],stdin=subprocess.PIPE,text=True,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        p.communicate(input=text)
        if p.returncode != 0:
            raise RuntimeError(f"Piper exited with status {p.returncode}")
    except Exception as e:
        print("[TTS legacy]",e); jaw_chatter_fallback(text); return
    _amplify_wav_inplace(TTS_WAV,VOLUME)
    env=jaw_env_from_wav(TTS_WAV)
    t=threading.Thread(target=speak_wav_play,args=(TTS_WAV,),daemon=True); t.start()
    (jaw_chatter_fallback(text) if float(np.max(env) if env.size else 0.0)<0.05 else jaw_drive_by_env(env))
    t.join()

def speak_phrases_with_jaw(phrases,first_audio=None,abort=None):
    """Speak an iterable without releasing the output stream between phrases."""
    speaking=False
    seen=[]

    def report_first_audio(seconds):
        mqtt_pub("tts/first_audio",f"{seconds:.3f}")
        if first_audio is not None:
            first_audio(seconds)

    def marked_phrases():
        nonlocal speaking
        for phrase in phrases:
            phrase=str(phrase).strip()
            if not phrase: continue
            seen.append(phrase)
            if not speaking:
                speaking=True
                mqtt_pub("speaking","ON")
            yield phrase

    with _speech_lock:
        marked=marked_phrases()
        try:
            if _speech_engine is not None:
                try:
                    metrics=_speech_engine.speak_phrases(
                        marked,
                        stop_event=controller.stop_event if controller is not None else None,
                        first_audio=report_first_audio,
                    )
                    mqtt_pub("tts/speak_time",f"{metrics.total_seconds:.3f}")
                    mqtt_pub("tts/audio_time",f"{metrics.audio_seconds:.3f}")
                    return metrics
                except SpeechEngineError as e:
                    print("[TTS streaming]",e)
                    if e.audio_started or (controller is not None and controller.stop_event.is_set()):
                        if abort is not None: abort()
                        return
                    for phrase in seen:
                        _legacy_speak_with_jaw(phrase)
            for phrase in marked:
                _legacy_speak_with_jaw(phrase)
        finally:
            _jaw_set(JAW_REST_FRAC)
            if speaking:
                mqtt_pub("speaking","OFF")

def speak_with_jaw(text:str):
    if not text: return None
    return speak_phrases_with_jaw([text])

def _publish_llm_metrics(result):
    if result is None: return
    metrics=result.metrics
    mqtt_pub("llm/first_token",f"{metrics.first_token_seconds:.3f}")
    mqtt_pub("llm/first_phrase",f"{metrics.first_phrase_seconds:.3f}")
    mqtt_pub("llm/reply_time",f"{metrics.total_seconds:.3f}")
    mqtt_pub("llm/phrase_count",str(metrics.phrases_emitted))

def _publish_memory_turns(memory):
    mqtt_pub("llm/memory_turns",str(memory.turn_count if memory is not None else 0))

def stream_llm_reply(user_text:str,memory=None)->str:
    fallback="Arrr, I be old and forgetful — say it again, matey!"
    if _llm_client is None:
        controller.set_state(RuntimeState.SPEAKING)
        speak_with_jaw(fallback)
        return fallback

    history=memory.messages() if memory is not None else None
    reply=_llm_client.start_reply(user_text,controller.stop_event,history=history)
    delivered=[]

    def phrases():
        for phrase in reply:
            if not delivered:
                controller.set_state(RuntimeState.SPEAKING)
            delivered.append(phrase)
            yield phrase

    def first_audio_started(_tts_seconds):
        mqtt_pub("llm/first_audio",f"{time.monotonic()-reply.started_at:.3f}")

    try:
        speak_phrases_with_jaw(
            phrases(),
            first_audio=first_audio_started,
            abort=reply.cancel,
        )
    except Exception as e:
        print("[LLM speech pipeline]",e)
        reply.cancel()

    result=reply.result or reply.wait(timeout=0.25)
    _publish_llm_metrics(result)
    if result is not None and result.error:
        print("[LLM]",result.error)

    if controller.stop_event.is_set():
        reply.cancel()
        return result.text if result is not None else ""

    if not delivered:
        controller.set_state(RuntimeState.SPEAKING)
        speak_with_jaw(fallback)
        return fallback

    completed=(result.text if result is not None else " ".join(delivered)).strip()
    if memory is not None and memory.remember_reply(user_text,result):
        _publish_memory_turns(memory)
    return completed

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

def _recognized_text(result_json):
    try:
        txt=(json.loads(result_json).get("text") or "").strip()
    except Exception:
        return ""
    return txt if txt and len(txt.split())>=MIN_TEXT_LEN else ""

def record_once(input_index:int,capture_rate:int,timeout_s:float,stop_event=None)->str:
    if not stt_enabled or sd is None or _VOSK_MODEL is None: return ""
    rec=vosk.KaldiRecognizer(_VOSK_MODEL,VOSK_RATE); rec.SetWords(True)
    q=queue.Queue(maxsize=128); rs_state={"prev":np.zeros(0,np.int16),"phase":0.0}
    gate=SpeechGate(
        sample_rate=VOSK_RATE,
        energy_threshold=ENERGY_GATE,
        preroll_seconds=PREROLL_SEC,
        minimum_voiced_seconds=MIN_VOICED_SEC,
        end_silence_seconds=END_SILENCE_SEC,
    )
    start_deadline=time.monotonic()+timeout_s
    utterance_deadline=None
    def cb(indata,frames,time_info,status):
        try: q.put_nowait(bytes(indata))
        except queue.Full: pass
    with sd.RawInputStream(samplerate=capture_rate,blocksize=SD_BLOCKSIZE,device=input_index,dtype="int16",channels=1,callback=cb):
        while True:
            now=time.monotonic()
            if stop_event is not None and stop_event.is_set(): return ""
            if not gate.speaking and now>=start_deadline: break
            if gate.speaking and (
                (utterance_deadline is not None and now>=utterance_deadline)
                or gate.silence_complete(now)
            ):
                break
            try: data=q.get(timeout=0.10)
            except queue.Empty: continue
            chunk=np.frombuffer(data,dtype=np.int16)
            rs=resample_linear_int16(chunk,capture_rate,VOSK_RATE,rs_state)
            gated=gate.process(rs,now)
            if gated.speech_started:
                utterance_deadline=now+MAX_UTTERANCE_SEC
            if gated.audio.size and rec.AcceptWaveform(gated.audio.tobytes()):
                txt=_recognized_text(rec.Result())
                if txt: return txt
    return _recognized_text(rec.FinalResult())

motion_enabled=True; motion_count=0
_motion_timer=None; _motion_timer_lock=threading.Lock()

def _cancel_motion_timer():
    global _motion_timer
    with _motion_timer_lock:
        if _motion_timer is not None:
            _motion_timer.cancel()
            _motion_timer=None

def _confirm_motion():
    global _motion_timer
    with _motion_timer_lock:
        _motion_timer=None
    if motion_enabled and getattr(pir,"motion_detected",False):
        _enqueue(EventKind.TRIGGER,source="pir")

def _schedule_motion_trigger():
    global _motion_timer
    with _motion_timer_lock:
        if _motion_timer is not None:
            _motion_timer.cancel()
        _motion_timer=threading.Timer(MOTION_HOLD_SEC,_confirm_motion)
        _motion_timer.daemon=True
        _motion_timer.start()

class _DummyPIR:
    motion_detected=False
    def close(self): pass
pir=None
if gpiozero is not None:
    try:
        pir=gpiozero.MotionSensor(PIR_PIN,queue_len=5,sample_rate=25,threshold=0.5)
        def _pir_on():
            global motion_count; motion_count+=1
            mqtt_pub("motion","ON"); mqtt_pub("motion/count",str(motion_count))
            _schedule_motion_trigger()
        def _pir_off():
            mqtt_pub("motion","OFF"); _cancel_motion_timer()
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

def publish_mqtt_discovery():
    for topic,payload in discovery_messages(DEVICE_NAME):
        mqtt_pub_abs(topic,"" if payload is None else json.dumps(payload),retain=True)

night_mode=False; _day={"listen":None,"speak":None,"vol":None}
def _set_eyes_dim(v):
    global EYES_LISTEN_FRAC; EYES_LISTEN_FRAC=clamp(v,0,1); eyes_listen(); mqtt_pub("eyes/dim",str(int(round(100*EYES_LISTEN_FRAC))))
def _set_eyes_full(v):
    global EYES_SPEAK_FRAC;  EYES_SPEAK_FRAC=clamp(v,0,1); eyes_speak(); mqtt_pub("eyes/full",str(int(round(100*EYES_SPEAK_FRAC))))
def _set_volume(v):
    global VOLUME; VOLUME=clamp(v,0,2); mqtt_pub("volume",str(int(round(100*VOLUME))))
def _set_motion_enabled(payload):
    global motion_enabled; motion_enabled=str(payload).lower() in ("on","true","1","yes")
    if not motion_enabled: _cancel_motion_timer()
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

def _do_mqtt_connect():
    if mqttc is not None:
        mqtt_connect()
        mqtt_pub("ready","ON")
        mqtt_pub("llm/memory_turns","0")

def _warm_ollama():
    if not requests: return
    try:
        requests.post(OLLAMA_URL,json={"model":OLLAMA_MODEL,"messages":[{"role":"user","content":"warming"}],"stream":False,
                                       "keep_alive":KEEP_ALIVE,"options":{"num_predict":1}},timeout=(2,10))
    except Exception as e:
        print("[ollama warmup]",e)

def _init_llm_client():
    global _llm_client
    if requests is None:
        print("[LLM] requests unavailable; using spoken fallback")
        return
    _llm_client=OllamaStreamingClient(
        http_client=requests,
        url=OLLAMA_URL,
        model=OLLAMA_MODEL,
        system_prompt=SYSTEM_PROMPT,
        keep_alive=KEEP_ALIVE,
        options=OLLAMA_OPTS,
        timeout=OLLAMA_TIMEOUT,
        minimum_phrase_chars=LLM_PHRASE_MIN_CHARS,
        soft_phrase_chars=LLM_PHRASE_SOFT_CHARS,
        maximum_phrase_chars=LLM_PHRASE_MAX_CHARS,
    )

def _configured_output_device():
    if AUDIO_OUTPUT_DEVICE is None: return None
    try: return int(AUDIO_OUTPUT_DEVICE)
    except ValueError: return AUDIO_OUTPUT_DEVICE

def _init_speech_engine():
    global _speech_engine
    if sd is None:
        print("[TTS] sounddevice unavailable; using legacy Piper process")
        mqtt_pub("tts/engine","legacy",retain=True)
        return
    started=time.monotonic()
    try:
        _speech_engine=PiperSpeechEngine.load(
            model_path=PIPER_MODEL,
            config_path=PIPER_CONFIG,
            audio_module=sd,
            jaw_set=_jaw_set,
            volume_getter=lambda: VOLUME,
            rest_fraction=JAW_REST_FRAC,
            maximum_fraction=JAW_MAX_FRAC,
            output_device=_configured_output_device(),
            frame_ms=TTS_FRAME_MS,
        )
        loaded_at=time.monotonic()
        warmup_seconds=_speech_engine.warm_up()
        elapsed=loaded_at-started
        mqtt_pub("tts/engine","streaming",retain=True)
        mqtt_pub("tts/model_load_time",f"{elapsed:.3f}",retain=True)
        mqtt_pub("tts/warmup_time",f"{warmup_seconds:.3f}",retain=True)
        print(
            f"[TTS] Piper voice warm and output stream ready in "
            f"{elapsed + warmup_seconds:.3f}s"
        )
    except Exception as e:
        _speech_engine=None
        mqtt_pub("tts/engine","legacy",retain=True)
        print(f"[TTS] warm engine unavailable; using legacy Piper process: {e}")

def _runtime_state_changed(state):
    mqtt_pub("status",state.value,retain=True)
    if state in (RuntimeState.GREETING,RuntimeState.SPEAKING):
        eyes_speak()
    elif state in (RuntimeState.LISTENING,RuntimeState.THINKING):
        eyes_listen()
    elif state is RuntimeState.EFFECT:
        return
    else:
        eyes_idle()

def _say_goodbye():
    gb=random.choice(GOODBYE_LINES) if GOODBYE_LINES else "Goodbye."
    _transcript_add("assistant",gb)
    controller.set_state(RuntimeState.SPEAKING)
    speak_with_jaw(gb)

def _conversation_loop(memory):
    while not controller.stop_event.is_set():
        controller.set_state(RuntimeState.LISTENING)
        text=record_once(
            in_idx or 0,
            44100,
            SPEECH_START_TIMEOUT,
            controller.stop_event,
        )
        if controller.stop_event.is_set(): return
        if not text:
            _say_goodbye(); return
        _transcript_add("user",text)
        if EXIT_RE.search(text):
            _say_goodbye(); return
        controller.set_state(RuntimeState.THINKING)
        reply=stream_llm_reply(text,memory)
        if controller.stop_event.is_set(): return
        _transcript_add("assistant",reply)

_last_talk=0.0
def _manual_trigger():
    global _last_talk
    now=time.monotonic()
    if (now-_last_talk)<MOTION_COOLDOWN_SEC:
        controller.set_state(RuntimeState.COOLDOWN)
        return
    _transcript_start()
    memory=None
    try:
        opener=pick_opening_line()
        memory=ConversationMemory(LLM_MEMORY_TURNS,opening_line=opener)
        _publish_memory_turns(memory)
        _transcript_add("assistant",opener)
        controller.set_state(RuntimeState.GREETING)
        speak_with_jaw(opener)
        _conversation_loop(memory)
    finally:
        if memory is not None: memory.clear()
        _publish_memory_turns(None)
        _last_talk=time.monotonic()
        _transcript_publish_and_clear()
        if not controller.stop_event.is_set():
            controller.set_state(RuntimeState.COOLDOWN)

def _handle_event(event):
    if event.kind is EventKind.TRIGGER:
        _manual_trigger()
    elif event.kind is EventKind.SAY:
        _transcript_start()
        try:
            _transcript_add("assistant",str(event.payload))
            controller.set_state(RuntimeState.SPEAKING)
            speak_with_jaw(str(event.payload))
        finally:
            _transcript_publish_and_clear()
    elif event.kind is EventKind.BLINK:
        controller.set_state(RuntimeState.EFFECT)
        eyes_blink(6,120,blocking=True)
    elif event.kind is EventKind.FLICKER:
        controller.set_state(RuntimeState.EFFECT)
        eyes_flicker(5.0,0.2,0.7,60,blocking=True)
    elif event.kind is EventKind.SET_EYES_DIM:
        _set_eyes_dim(float(event.payload))
    elif event.kind is EventKind.SET_EYES_FULL:
        _set_eyes_full(float(event.payload))
    elif event.kind is EventKind.SET_VOLUME:
        _set_volume(float(event.payload))
    elif event.kind is EventKind.SET_MOTION_ENABLED:
        _set_motion_enabled(event.payload)
    elif event.kind is EventKind.SET_NIGHT_MODE:
        _toggle_night_mode(event.payload)
    elif event.kind is EventKind.RESTART:
        mqtt_pub("availability","offline",retain=True)
        controller.request_stop("mqtt-restart")

    if not controller.stop_event.is_set():
        controller.set_state(RuntimeState.IDLE)

def _signal_handler(sig,frame):
    print(f"[signal] {signal.Signals(sig).name}; stopping")
    if controller is not None:
        controller.request_stop("signal")

def _cleanup():
    _cancel_motion_timer()
    try: _transcript_publish_and_clear()
    except Exception: pass
    try: mqtt_pub("ready","OFF",retain=True)
    except Exception: pass
    try: mqtt_pub("availability","offline",retain=True)
    except Exception: pass
    try: _jaw_set(JAW_REST_FRAC)
    except Exception: pass
    try:
        if _speech_engine: _speech_engine.close()
    except Exception: pass
    try: eyes_off()
    except Exception: pass
    try: pir.close()
    except Exception: pass
    try:
        if _pca: _pca.deinit()
    except Exception: pass
    try:
        if mqttc: mqttc.loop_stop(); mqttc.disconnect()
    except Exception: pass

def main():
    global controller
    controller=SkeletonController(_handle_event,_runtime_state_changed)
    signal.signal(signal.SIGINT,_signal_handler)
    signal.signal(signal.SIGTERM,_signal_handler)
    _do_mqtt_connect()
    _init_speech_engine()
    _init_llm_client()
    _warm_ollama()
    print("👀 Waiting for motion or MQTT commands…")
    try:
        controller.run_forever()
    except Exception as e:
        print("[controller]",e)
        controller.request_stop("controller-error")
        raise
    finally:
        _cleanup()

if __name__ == "__main__":
    main()
