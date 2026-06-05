"""
DreamForge v2 — Auto Key Rotation System
==========================================
Multiple API keys with automatic failover:
- Gemini keys: 3 keys, rotate on quota/error
- Groq keys: 3 keys, rotate on rate limit
- Pixabay: 1 key
- Pexels: 1 key  
- ElevenLabs: 4 keys, rotate on quota
- HuggingFace: 3 keys, rotate on error

pip install flask flask-cors requests python-dotenv edge-tts gtts google-generativeai
python app.py
"""

import os, uuid, json, time, requests, threading, subprocess, random
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__, static_folder="static")
CORS(app)

PORT = int(os.getenv("PORT", 5000))

for d in ["outputs/clips","outputs/images","outputs/audio","outputs/thumbnails","uploads"]:
    os.makedirs(d, exist_ok=True)

# ═══════════════════════════════════════════════════
# KEY ROTATION SYSTEM
# ═══════════════════════════════════════════════════
class KeyRotator:
    def __init__(self, keys: list, name: str):
        self.keys   = [k.strip() for k in keys if k.strip()]
        self.name   = name
        self.index  = 0
        self.errors = {}  # key -> error_count
        self.lock   = threading.Lock()

    def get(self):
        with self.lock:
            if not self.keys: return None
            # find next working key
            for _ in range(len(self.keys)):
                key = self.keys[self.index % len(self.keys)]
                if self.errors.get(key, 0) < 3:
                    return key
                self.index += 1
            # all failed — reset errors and try again
            self.errors = {}
            return self.keys[0] if self.keys else None

    def mark_failed(self, key):
        with self.lock:
            self.errors[key] = self.errors.get(key, 0) + 1
            self.index = (self.index + 1) % max(len(self.keys), 1)
            print(f"[KeyRotator] {self.name}: key failed ({self.errors[key]}/3), switching to next")

    def mark_success(self, key):
        with self.lock:
            self.errors[key] = 0

    def status(self):
        return {"total": len(self.keys), "active": sum(1 for k in self.keys if self.errors.get(k,0)<3), "name": self.name}

# ── PARSE KEYS FROM ENV ──────────────────────────
def parse_keys(env_var, default=""):
    val = os.getenv(env_var, default)
    return [k.strip() for k in val.split(",") if k.strip()]

# Initialize key rotators
GEMINI  = KeyRotator(parse_keys("GEMINI_KEYS"), "Gemini")
GROQ    = KeyRotator(parse_keys("GROQ_KEYS"),   "Groq")
EL      = KeyRotator(parse_keys("EL_KEYS"),     "ElevenLabs")
HF      = KeyRotator(parse_keys("HF_KEYS"),     "HuggingFace")
PIXABAY = os.getenv("PIXABAY_KEY", "")
PEXELS  = os.getenv("PEXELS_KEY", "")

# ═══════════════════════════════════════════════════
# VISUAL STYLES
# ═══════════════════════════════════════════════════
VISUAL_STYLES = {
    "none":           "",
    "realistic":      "photorealistic film photography, cinematic, 8K, ultra detailed",
    "cartoon3d":      "3D cartoon animation, Pixar style, vibrant colors, smooth render",
    "photograph":     "professional DSLR photography, bokeh background, sharp focus",
    "whimsical":      "whimsical fairy tale illustration, soft pastel colors, dreamy",
    "felt_dolls":     "felt doll craft art, handmade textile, cozy aesthetic",
    "crayon":         "crayon drawing style, childlike colorful art, textured paper",
    "lovecraftian":   "Lovecraftian horror, dark cosmic, mysterious ancient",
    "urban_sketch":   "urban sketch ink pen drawing, architectural illustration",
    "dark_deco":      "dark art deco, geometric noir, 1920s glamour, gold accents",
    "gta4":           "GTA 4 game style, realistic urban gritty, cinematic",
    "toon_shader":    "toon shader cel animation, bold outlines, flat colors",
    "noir_comic":     "noir comic book black white ink, dramatic shadows, graphic novel",
    "ink_watercolor": "ink watercolor painting, flowing translucent colors, artistic",
    "modern_real":    "modern realism, contemporary painting style, masterpiece",
    "futuristic":     "sci-fi futuristic neon holographic, advanced technology",
    "ghibli":         "Studio Ghibli anime style, lush nature hand-drawn, Miyazaki masterpiece, detailed backgrounds",
    "anime":          "anime illustration detailed, Makoto Shinkai style, beautiful lighting",
    "pixel_90s":      "90s pixel art, retro video game style, 8-bit aesthetic",
    "low_poly":       "low poly 3D geometric art, clean minimalist",
    "cross_stitch":   "cross stitch embroidery textile pattern, handmade craft",
    "epic_fantasy":   "epic fantasy digital painting, dramatic lighting, mystical",
    "jurassic":       "prehistoric jurassic world, ancient dramatic landscape",
    "clay":           "claymation stop motion, clay sculpture, handmade",
    "impressionist":  "impressionist oil painting, Monet brushstrokes, dreamy light",
    "us_comic":       "American comic book superhero, bold colors, dynamic pose",
    "horror":         "horror movie dark atmosphere, terrifying, suspenseful, moody",
    "cyberpunk":      "cyberpunk neon city rain night, dystopia, Blade Runner style",
    "spooky":         "spooky atmospheric photography, moody Halloween, eerie",
    "neoclassic":     "neoclassical art marble sculpture, ancient Greek Roman",
    "prehistoric":    "prehistoric cave painting, ancient primitive art, ochre",
    "roman_art":      "Roman Renaissance oil painting, classical masters, dramatic",
    "nature_photo":   "professional nature wildlife photography, golden hour, vivid",
}

VOICES = {
    "none":            "",
    "aria":            "en-US-AriaNeural",
    "guy":             "en-US-GuyNeural",
    "jenny":           "en-US-JennyNeural",
    "tony":            "en-US-TonyNeural",
    "musing_female":   "en-GB-SoniaNeural",
    "calm":            "en-US-AnaNeural",
    "dramatic_male":   "en-US-DavisNeural",
    "sara_urdu":       "ur-PK-UzmaNeural",
    "asad_urdu":       "ur-PK-AsadNeural",
    "hindi_female":    "hi-IN-SwaraNeural",
    "hindi_male":      "hi-IN-MadhurNeural",
}

# ═══════════════════════════════════════════════════
# AI HELPERS — with key rotation
# ═══════════════════════════════════════════════════

def gemini_generate(system, user, max_tokens=4000, temp=0.8):
    """Gemini with auto key rotation"""
    for attempt in range(len(GEMINI.keys) + 1):
        key = GEMINI.get()
        if not key: break
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": f"{system}\n\n{user}"}]}],
                      "generationConfig": {"temperature": temp, "maxOutputTokens": max_tokens}},
                timeout=50
            )
            if r.status_code == 429 or r.status_code == 403:
                GEMINI.mark_failed(key)
                continue
            if r.status_code == 200:
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                GEMINI.mark_success(key)
                return text
            GEMINI.mark_failed(key)
        except Exception as e:
            GEMINI.mark_failed(key)
    return None

def groq_generate(system, user, max_tokens=4000, temp=0.8):
    """Groq with auto key rotation"""
    for attempt in range(len(GROQ.keys) + 1):
        key = GROQ.get()
        if not key: break
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile",
                      "messages": [{"role":"system","content":system},{"role":"user","content":user}],
                      "temperature": temp, "max_tokens": max_tokens},
                timeout=50
            )
            if r.status_code == 429:
                GROQ.mark_failed(key); continue
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"].strip()
                GROQ.mark_success(key)
                return text
            GROQ.mark_failed(key)
        except Exception as e:
            GROQ.mark_failed(key)
    return None

def ai_generate(system, user, max_tokens=4000, temp=0.8):
    """Try Gemini first, fallback to Groq"""
    result = gemini_generate(system, user, max_tokens, temp)
    if result:
        return result, "gemini"
    result = groq_generate(system, user, max_tokens, temp)
    if result:
        return result, "groq"
    return None, None

def ai_json(system, user, max_tokens=4000, temp=0.8):
    """Get JSON from AI with fallback"""
    raw, engine = ai_generate(system, user, max_tokens, temp)
    if raw:
        cleaned = raw.replace("```json","").replace("```","").strip()
        # find JSON in response
        start = cleaned.find("{")
        end   = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end]), engine
            except: pass
        try:
            return json.loads(cleaned), engine
        except: pass
    return None, None

# ═══════════════════════════════════════════════════
# IMAGE HELPERS
# ═══════════════════════════════════════════════════
def poll_image(prompt, w=1280, h=720, seed=None):
    seed = seed or random.randint(1, 99999)
    clean = prompt[:500].replace(" ","%20").replace("&","and").replace("#","").replace("'","").replace('"','')
    url = f"https://image.pollinations.ai/prompt/{clean}?width={w}&height={h}&seed={seed}&nologo=true&enhance=true&model=flux"
    try:
        r = requests.get(url, timeout=80)
        if r.status_code == 200 and len(r.content) > 2000:
            fname = f"images/{uuid.uuid4().hex[:10]}.jpg"
            with open(f"outputs/{fname}", "wb") as f: f.write(r.content)
            return f"/outputs/{fname}"
    except: pass
    return None

def hf_image(prompt, w=1280, h=720):
    """HuggingFace SDXL with key rotation"""
    for _ in range(len(HF.keys) + 1):
        key = HF.get()
        if not key: break
        try:
            r = requests.post(
                "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0",
                headers={"Authorization": f"Bearer {key}"},
                json={"inputs": prompt[:500], "parameters": {"width": min(w,1024), "height": min(h,1024)}},
                timeout=90
            )
            if r.status_code == 200 and "image" in r.headers.get("content-type",""):
                fname = f"images/hf_{uuid.uuid4().hex[:10]}.jpg"
                with open(f"outputs/{fname}", "wb") as f: f.write(r.content)
                HF.mark_success(key)
                return f"/outputs/{fname}"
            HF.mark_failed(key)
        except: HF.mark_failed(key)
    return None

def edge_tts_gen(text, voice="en-US-AriaNeural", scene_id=0):
    """Edge-TTS free Microsoft voiceover"""
    try:
        fname  = f"audio/tts_{scene_id}_{uuid.uuid4().hex[:8]}.mp3"
        fpath  = f"outputs/{fname}"
        script = f"""import asyncio, edge_tts
async def main():
    t = edge_tts.Communicate(text={json.dumps(text)}, voice="{voice}", rate="-5%")
    await t.save("{fpath}")
asyncio.run(main())"""
        tmp = f"/tmp/tts_{uuid.uuid4().hex[:6]}.py"
        with open(tmp,"w") as f: f.write(script)
        subprocess.run(["python3", tmp], timeout=35, capture_output=True)
        os.remove(tmp)
        if os.path.exists(fpath) and os.path.getsize(fpath) > 500:
            return fpath, fname
    except: pass
    return None, None

def el_tts(text, scene_id=0):
    """ElevenLabs TTS with key rotation"""
    for _ in range(len(EL.keys) + 1):
        key = EL.get()
        if not key: break
        try:
            r = requests.post(
                "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM",
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                json={"text": text, "model_id": "eleven_monolingual_v1",
                      "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}},
                timeout=30
            )
            if r.status_code == 200:
                fname = f"audio/el_{scene_id}_{uuid.uuid4().hex[:8]}.mp3"
                with open(f"outputs/{fname}", "wb") as f: f.write(r.content)
                EL.mark_success(key)
                return f"/outputs/{fname}", "elevenlabs"
            if r.status_code in [401, 429]:
                EL.mark_failed(key)
        except: EL.mark_failed(key)
    return None, None

# ═══════════════════════════════════════════════════
# STATIC
# ═══════════════════════════════════════════════════
@app.route("/")
def index(): return send_from_directory("static", "index.html")

@app.route("/outputs/<path:f>")
def serve_out(f): return send_from_directory("outputs", f)

@app.route("/uploads/<path:f>")
def serve_up(f): return send_from_directory("uploads", f)

# ═══════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════
@app.route("/api/status")
def status():
    return jsonify({
        "ok": True,
        "apis": {
            "gemini":      {**GEMINI.status(), "label": "Gemini 1.5 Flash", "note": "Primary AI — 1500/day per key"},
            "groq":        {**GROQ.status(),   "label": "Groq llama-3.3",   "note": "Fallback AI — 30/min per key"},
            "pollinations":{"ok": True, "total": 1, "active": 1, "label": "Pollinations", "note": "Images — unlimited free"},
            "pixabay":     {"ok": bool(PIXABAY), "total": 1, "active": int(bool(PIXABAY)), "label": "Pixabay", "note": "Stock video — 5000/hr"},
            "pexels":      {"ok": bool(PEXELS),  "total": 1, "active": int(bool(PEXELS)),  "label": "Pexels",  "note": "Stock video — 200/hr"},
            "elevenlabs":  {**EL.status(), "label": "ElevenLabs TTS", "note": "Pro voice — 10k/mo per key"},
            "huggingface": {**HF.status(), "label": "HuggingFace",    "note": "AI video clips"},
            "edge_tts":    {"ok": True, "total": 1, "active": 1, "label": "Edge TTS", "note": "Free unlimited Microsoft voices"},
        },
        "styles": list(VISUAL_STYLES.keys()),
        "voices": list(VOICES.keys())
    })

# ═══════════════════════════════════════════════════
# 1. GENERATE BRIEF
# ═══════════════════════════════════════════════════
@app.route("/api/brief/generate", methods=["POST"])
def gen_brief():
    d        = request.json
    prompt   = d.get("prompt","")
    style    = d.get("style","none")
    voice    = d.get("voice","aria")
    aspect   = d.get("aspect","16:9")
    dur      = d.get("duration","auto")
    platform = d.get("platform","youtube")
    mode     = d.get("mode","full_video")
    lang     = d.get("language","English")

    dur_map  = {"15s":15,"30s":30,"1 min":60,"3 min":180,"5 min":300,"10 min":600,"auto":60}
    total_s  = dur_map.get(dur, 60)
    n_scenes = max(3, min(total_s // 8, 15))
    sec_each = total_s // n_scenes
    style_p  = VISUAL_STYLES.get(style, "")
    w, h     = (720,1280) if aspect=="9:16" else (1280,720) if aspect in ["16:9","21:9"] else (1080,1080)

    system = "You are a professional AI video director. Create cinematic video scripts. Reply ONLY with valid JSON, no markdown fences."
    user = f"""Create a complete cinematic video production brief.

USER PROMPT: {prompt}
VISUAL STYLE: {style} — {style_p}
NARRATOR VOICE TONE: {voice}
ASPECT RATIO: {aspect}
TOTAL DURATION: {total_s}s ({n_scenes} scenes × {sec_each}s each)
PLATFORM: {platform}
MODE: {mode}
LANGUAGE: {lang}

Return ONLY this JSON:
{{
  "title": "SEO optimized video title",
  "theme": "theme in 5 words",
  "purpose": "one sentence purpose",
  "audience": "target audience description",
  "platform": "{platform}",
  "totalDuration": {total_s},
  "aspectRatio": "{aspect}",
  "visualStyle": "{style}",
  "voice": "{voice}",
  "music": "ambient/dramatic/upbeat/emotional/cinematic/orchestral",
  "captions": true,
  "scenes": [
    {{
      "id": 1,
      "title": "scene title",
      "duration": {sec_each},
      "narration": "3-5 engaging sentences in {lang}, {voice} style, storyteller tone",
      "onScreen": "punchy on-screen text max 8 words",
      "imagePrompt": "{style_p}, {prompt}, scene 1, cinematic masterpiece, ultra detailed 4K, no text, no watermark",
      "videoQuery": "2-3 word stock video search term",
      "camera": "wide establishing/aerial drone/slow close-up/tracking/POV",
      "transition": "fade/zoom/slide/dissolve/cut",
      "colorGrade": "warm golden/cold blue/vibrant/desaturated/dramatic/teal-orange"
    }}
  ],
  "hook": "ultra engaging first 3 seconds hook",
  "callToAction": "end screen CTA",
  "description": "platform description with emojis 150 chars",
  "hashtags": ["#tag1","#tag2","#tag3","#tag4","#tag5","#tag6","#tag7","#tag8","#tag9","#tag10"]
}}"""

    data, engine = ai_json(system, user)

    if data:
        return jsonify({"ok": True, "brief": data, "w": w, "h": h, "engine": engine})

    # Local fallback
    scenes = [{"id":i+1,"title":f"Scene {i+1}","duration":sec_each,
               "narration":f"{prompt}. Scene {i+1} unfolds with breathtaking {style} visuals.",
               "onScreen":f"Part {i+1}","imagePrompt":f"{style_p} {prompt} scene {i+1}, cinematic 4K",
               "videoQuery":prompt[:25],"camera":"wide shot","transition":"fade","colorGrade":"cinematic"}
               for i in range(n_scenes)]
    return jsonify({"ok":True,"brief":{
        "title":prompt[:60],"theme":prompt[:30],"purpose":"Engage viewers",
        "audience":"General audience","platform":platform,"totalDuration":total_s,
        "aspectRatio":aspect,"visualStyle":style,"voice":voice,"music":"ambient",
        "captions":True,"scenes":scenes,"hook":"Watch this!",
        "callToAction":"Like & Subscribe!","description":f"{prompt} #viral","hashtags":["#viral","#ai"]
    },"w":w,"h":h,"engine":"local"})

# ═══════════════════════════════════════════════════
# 2. GENERATE IMAGE
# ═══════════════════════════════════════════════════
@app.route("/api/image/generate", methods=["POST"])
def gen_image():
    d        = request.json
    prompt   = d.get("prompt","")
    style    = d.get("style","none")
    w        = int(d.get("w",1280))
    h        = int(d.get("h",720))
    scene_id = d.get("sceneId",1)
    seed     = d.get("seed", scene_id * 137 % 99999)

    style_sfx  = VISUAL_STYLES.get(style,"")
    full_prompt = f"{prompt}, {style_sfx}" if style_sfx else prompt

    # Pollinations first (unlimited free)
    url = poll_image(full_prompt, w, h, seed)
    if url: return jsonify({"ok":True,"url":url,"sceneId":scene_id,"engine":"pollinations"})

    # HF SDXL fallback
    url = hf_image(full_prompt, w, h)
    if url: return jsonify({"ok":True,"url":url,"sceneId":scene_id,"engine":"huggingface"})

    return jsonify({"ok":False,"sceneId":scene_id,"error":"Image generation failed"})

# ═══════════════════════════════════════════════════
# 3. GENERATE CLIP
# ═══════════════════════════════════════════════════
@app.route("/api/clip/generate", methods=["POST"])
def gen_clip():
    d        = request.json
    prompt   = d.get("prompt","")
    query    = d.get("videoQuery", prompt[:40])
    scene_id = d.get("sceneId",1)
    w        = int(d.get("w",1280))
    h        = int(d.get("h",720))
    orient   = "vertical" if h > w else "horizontal"

    # HF Text-to-Video
    for _ in range(len(HF.keys)+1):
        key = HF.get()
        if not key: break
        try:
            r = requests.post(
                "https://api-inference.huggingface.co/models/ali-vilab/text-to-video-ms-1.7b",
                headers={"Authorization": f"Bearer {key}"},
                json={"inputs": prompt[:400], "parameters": {"num_frames":16,"num_inference_steps":20}},
                timeout=130
            )
            if r.status_code==200 and "video" in r.headers.get("content-type",""):
                fname = f"clips/hf_{scene_id}_{uuid.uuid4().hex[:8]}.mp4"
                with open(f"outputs/{fname}","wb") as f: f.write(r.content)
                HF.mark_success(key)
                return jsonify({"ok":True,"type":"video","url":f"/outputs/{fname}","sceneId":scene_id,"engine":"huggingface"})
            HF.mark_failed(key)
        except: HF.mark_failed(key)

    # Pixabay stock
    if PIXABAY:
        try:
            r = requests.get("https://pixabay.com/api/videos/",
                params={"key":PIXABAY,"q":query,"per_page":5,"orientation":orient,"safesearch":"true","min_duration":3},timeout=12)
            if r.status_code==200:
                hits = r.json().get("hits",[])
                if hits:
                    vf = hits[0].get("videos",{})
                    best = vf.get("large") or vf.get("medium") or vf.get("small")
                    if best and best.get("url"):
                        vr = requests.get(best["url"],timeout=40,stream=True)
                        if vr.status_code==200:
                            fname = f"clips/pixabay_{scene_id}_{uuid.uuid4().hex[:8]}.mp4"
                            with open(f"outputs/{fname}","wb") as f:
                                for chunk in vr.iter_content(8192): f.write(chunk)
                            return jsonify({"ok":True,"type":"video","url":f"/outputs/{fname}","sceneId":scene_id,"engine":"pixabay"})
        except: pass

    # Pexels stock
    if PEXELS:
        try:
            r = requests.get("https://api.pexels.com/videos/search",
                headers={"Authorization":PEXELS},
                params={"query":query,"per_page":3,"orientation":orient},timeout=12)
            if r.status_code==200:
                vids = r.json().get("videos",[])
                if vids:
                    vf = sorted(vids[0].get("video_files",[]),key=lambda x:x.get("width",0),reverse=True)
                    if vf and vf[0].get("link"):
                        vr = requests.get(vf[0]["link"],timeout=40,stream=True)
                        if vr.status_code==200:
                            fname = f"clips/pexels_{scene_id}_{uuid.uuid4().hex[:8]}.mp4"
                            with open(f"outputs/{fname}","wb") as f:
                                for chunk in vr.iter_content(8192): f.write(chunk)
                            return jsonify({"ok":True,"type":"video","url":f"/outputs/{fname}","sceneId":scene_id,"engine":"pexels"})
        except: pass

    # Image fallback
    style = d.get("style","none")
    url = poll_image(prompt, w, h, scene_id*137%99999)
    if url: return jsonify({"ok":True,"type":"image","url":url,"sceneId":scene_id,"engine":"pollinations"})
    return jsonify({"ok":False,"sceneId":scene_id,"error":"No clip source"})

# ═══════════════════════════════════════════════════
# 4. TTS
# ═══════════════════════════════════════════════════
@app.route("/api/tts/generate", methods=["POST"])
def gen_tts():
    d        = request.json
    text     = d.get("text","")
    voice_id = d.get("voice","aria")
    scene_id = d.get("sceneId",0)
    edge_v   = VOICES.get(voice_id,"en-US-AriaNeural")

    # ElevenLabs (best quality, with rotation)
    if voice_id not in ["none","sara_urdu","asad_urdu","hindi_female","hindi_male"]:
        url, eng = el_tts(text, scene_id)
        if url: return jsonify({"ok":True,"url":url,"engine":eng})

    # Edge-TTS (free Microsoft)
    if edge_v:
        fpath, fname = edge_tts_gen(text, edge_v, scene_id)
        if fpath: return jsonify({"ok":True,"url":f"/outputs/{fname}","engine":"edge_tts"})

    # gTTS fallback
    try:
        from gtts import gTTS
        lang_map={"ur-PK-UzmaNeural":"ur","ur-PK-AsadNeural":"ur","hi-IN-SwaraNeural":"hi","hi-IN-MadhurNeural":"hi"}
        lang=lang_map.get(edge_v,"en")
        fname=f"audio/gtts_{scene_id}_{uuid.uuid4().hex[:8]}.mp3"
        gTTS(text=text,lang=lang,slow=False).save(f"outputs/{fname}")
        return jsonify({"ok":True,"url":f"/outputs/{fname}","engine":"gtts"})
    except: pass

    return jsonify({"ok":False,"error":"TTS failed. pip install edge-tts gtts"})

# ═══════════════════════════════════════════════════
# 5. THUMBNAIL
# ═══════════════════════════════════════════════════
@app.route("/api/thumbnail/generate", methods=["POST"])
def gen_thumb():
    d     = request.json
    idea  = d.get("prompt","")
    style = d.get("style","realistic")
    w     = int(d.get("w",1280))
    h     = int(d.get("h",720))
    sfx   = VISUAL_STYLES.get(style,"")
    prompt= f"YouTube thumbnail, {idea}, {sfx}, dramatic lighting, vibrant, no text, professional 4K"
    url   = poll_image(prompt, w, h)
    if url: return jsonify({"ok":True,"url":url})
    return jsonify({"ok":False})

# ═══════════════════════════════════════════════════
# 6. STOCK SEARCH
# ═══════════════════════════════════════════════════
@app.route("/api/stock/search", methods=["POST"])
def stock_search():
    d       = request.json
    query   = d.get("query","nature")
    orient  = d.get("orientation","landscape")
    results = []

    if PIXABAY:
        try:
            r = requests.get("https://pixabay.com/api/videos/",
                params={"key":PIXABAY,"q":query,"per_page":6,"orientation":orient,"safesearch":"true"},timeout=10)
            if r.status_code==200:
                for v in r.json().get("hits",[]):
                    vf=v.get("videos",{})
                    best=vf.get("large") or vf.get("medium") or vf.get("small")
                    if best: results.append({"id":v["id"],"url":best.get("url",""),"thumb":f"https://i.vimeocdn.com/video/{v.get('picture_id','')}_295x166.jpg","source":"pixabay","type":"video","duration":v.get("duration",0)})
        except: pass

    if PEXELS and len(results)<6:
        try:
            r = requests.get("https://api.pexels.com/videos/search",
                headers={"Authorization":PEXELS},
                params={"query":query,"per_page":6-len(results),"orientation":orient},timeout=10)
            if r.status_code==200:
                for v in r.json().get("videos",[]):
                    vf=sorted(v.get("video_files",[]),key=lambda x:x.get("width",0),reverse=True)
                    if vf: results.append({"id":v["id"],"url":vf[0].get("link",""),"thumb":v.get("image",""),"source":"pexels","type":"video","duration":v.get("duration",0)})
        except: pass

    return jsonify({"ok":True,"results":results,"query":query})

# ═══════════════════════════════════════════════════
# 7. BATCH IMAGES
# ═══════════════════════════════════════════════════
@app.route("/api/batch/images", methods=["POST"])
def batch_images():
    d       = request.json
    scenes  = d.get("scenes",[])
    style   = d.get("style","none")
    w       = int(d.get("w",1280))
    h       = int(d.get("h",720))
    results = [None]*len(scenes)
    lock    = threading.Lock()

    def gen(i, sc):
        sfx   = VISUAL_STYLES.get(style,"")
        full  = f"{sc.get('imagePrompt','')}, {sfx}" if sfx else sc.get("imagePrompt","")
        url   = poll_image(full, w, h, (sc.get("id",i+1))*137%99999)
        with lock: results[i]={"ok":bool(url),"url":url,"sceneId":sc.get("id",i+1)}

    threads=[threading.Thread(target=gen,args=(i,sc)) for i,sc in enumerate(scenes)]
    for t in threads: t.start()
    for t in threads: t.join()
    return jsonify({"ok":True,"results":results})

# ═══════════════════════════════════════════════════
# 8. AI CHAT (streaming, Gemini + Groq)
# ═══════════════════════════════════════════════════
@app.route("/api/chat", methods=["POST"])
def chat():
    d    = request.json
    msgs = d.get("messages",[])
    sys_p= "You are DreamForge AI, a creative video director assistant for YouTube and TikTok. Help with scripts, ideas, hooks, titles, and video strategy."

    def gen_gemini():
        history = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in msgs[-6:]])
        full_prompt = f"{sys_p}\n\nConversation:\n{history}\n\nASSISTANT:"
        for _ in range(len(GEMINI.keys)+1):
            key = GEMINI.get()
            if not key: break
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}",
                    headers={"Content-Type":"application/json"},
                    json={"contents":[{"parts":[{"text":full_prompt}]}],"generationConfig":{"temperature":0.7,"maxOutputTokens":2048}},
                    timeout=45
                )
                if r.status_code==200:
                    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    GEMINI.mark_success(key)
                    # stream word by word
                    for word in text.split(" "):
                        yield f"data: {json.dumps({'content':word+' ','done':False})}\n\n"
                        time.sleep(0.02)
                    yield f"data: {json.dumps({'content':'','done':True})}\n\n"
                    return
                GEMINI.mark_failed(key)
            except: GEMINI.mark_failed(key)

        # Groq streaming fallback
        for _ in range(len(GROQ.keys)+1):
            key = GROQ.get()
            if not key: break
            try:
                with requests.post("https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
                    json={"model":"llama-3.3-70b-versatile","messages":[{"role":"system","content":sys_p}]+msgs,"stream":True,"temperature":0.7,"max_tokens":2048},
                    stream=True,timeout=60) as r:
                    if r.status_code==429: GROQ.mark_failed(key); continue
                    for line in r.iter_lines():
                        if not line: continue
                        line=line.decode("utf-8")
                        if line.startswith("data: "):
                            chunk=line[6:]
                            if chunk=="[DONE]": yield f"data: {json.dumps({'content':'','done':True})}\n\n"; return
                            try:
                                c=json.loads(chunk)["choices"][0]["delta"].get("content","")
                                yield f"data: {json.dumps({'content':c,'done':False})}\n\n"
                            except: continue
                GROQ.mark_success(key)
                return
            except: GROQ.mark_failed(key)

        yield f"data: {json.dumps({'content':'AI unavailable. Check API keys in .env','done':True})}\n\n"

    return Response(stream_with_context(gen_gemini()),mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# ═══════════════════════════════════════════════════
# 9. CAPTIONS
# ═══════════════════════════════════════════════════
@app.route("/api/captions/generate", methods=["POST"])
def gen_captions():
    d   = request.json
    res, eng = ai_json(
        "You are a social media expert. Reply ONLY with JSON.",
        f"""Create social media package.
TITLE: {d.get('title','')}
PLATFORM: {d.get('platform','tiktok')}
NICHE: {d.get('niche','lifestyle')}
LANGUAGE: {d.get('language','English')}
JSON: {{"caption":"engaging caption with emojis","hashtags":["#tag1","#tag2","#tag3","#tag4","#tag5","#tag6","#tag7","#tag8","#tag9","#tag10"],"hook":"first comment hook","cta":"call to action","bestTime":"best posting time","seoTitle":"SEO optimized title"}}"""
    )
    if res: return jsonify({"ok":True,"data":res,"engine":eng})
    return jsonify({"ok":False,"error":"Caption generation failed"})

if __name__ == "__main__":
    print(f"\n{'═'*58}")
    print("  DreamForge v2 — AI Video Studio with Key Rotation")
    print(f"  ➜  http://localhost:{PORT}")
    print(f"  Gemini:  {GEMINI.status()['active']}/{GEMINI.status()['total']} keys active")
    print(f"  Groq:    {GROQ.status()['active']}/{GROQ.status()['total']} keys active")
    print(f"  EL TTS:  {EL.status()['active']}/{EL.status()['total']} keys active")
    print(f"  HF:      {HF.status()['active']}/{HF.status()['total']} keys active")
    print(f"  Pixabay: {'✓' if PIXABAY else '✗'}")
    print(f"  Pexels:  {'✓' if PEXELS else '✗'}")
    print(f"  Images:  ✓ Pollinations (always free)")
    print(f"  TTS:     ✓ Edge-TTS (always free)")
    print(f"{'═'*58}\n")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
