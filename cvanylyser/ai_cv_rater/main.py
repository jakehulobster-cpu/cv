import os
import json
import textwrap
from pathlib import Path
from google import genai
from google.genai import types 
import time # Pievienots atpakaļ, lai izvairītos no ātruma ierobežojumiem

# --- SDK UN API ATSLĒGAS INITIALIZĀCIJA (MODIFICĒTA) ---

# UZMANĪBU: Nomainiet 'JŪSU_ATSLĒGA_ŠEIT' ar savu faktisko Gemini API atslēgu.
# Šis ir pagaidu risinājums, ja vides mainīgais GEMINI_API_KEY netiek atrasts.
MY_API_KEY = "AIzaSyDOYgNp9YLdSxODqtces3P2DADTpk3ce5A" 

if MY_API_KEY == "AIzaSyDOYgNp9YLdSxODqtces3P2DADTpk3ce5A" or not MY_API_KEY:
    # Ja atslēga joprojām nav ievietota tieši, mēģinām to nolasīt no vides mainīgā
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyDOYgNp9YLdSxODqtces3P2DADTpk3ce5A")
    if not GEMINI_API_KEY:
        raise RuntimeError("Lūdzu, ievietojiet savu API atslēgu tieši Python kodā (main.py) vai iestatiet vides mainīgo GEMINI_API_KEY.")
    MY_API_KEY = GEMINI_API_KEY # Izmantojam atrasto atslēgu

try:
    # Inicializējam SDK klientu, izmantojot atrasto/ievietoto atslēgu
    client = genai.Client(api_key=MY_API_KEY)
    print("✅ Gemini SDK klients inicializēts. Sākam darbu.")
except Exception as e:
    raise RuntimeError(f"Nevarēja inicializēt Gemini klientu: Pārbaudiet API atslēgas derīgumu. Kļūda: {e}")

# --- KONFIGURĀCIJA ---
MODEL_NAME = "gemini-2.5-flash"
TEMPERATURE = 0.2  

BASE_DIR = Path.cwd()
SAMPLE_DIR = BASE_DIR / "sample_inputs"
OUTPUT_DIR = BASE_DIR / "outputs"
PROMPT_DIR = BASE_DIR / "prompts"
OUTPUT_DIR.mkdir(exist_ok=True)
PROMPT_DIR.mkdir(exist_ok=True)

JD_PATH = SAMPLE_DIR / "jd.txt"
CV_PATHS = [SAMPLE_DIR / f"cv{i}.txt" for i in (1, 2, 3)]

REQUIRED_KEYS = ["match_score", "summary", "strengths", "missing_requirements", "verdict"]


# --- FUNKCIJAS (BEZ IZMAIŅĀM) ---

def read_text(path: Path) -> str:
    """Nolasa teksta failu."""
    try:
        with path.open("r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        raise FileNotFoundError(f"Fails nav atrasts: {path}")


def build_prompt(jd_text: str, cv_text: str, candidate_label: str = "Candidate") -> str:
    """Sagatavo oriģinālu Gemini promptu ar stingrām instrukcijām."""
    instruct = textwrap.dedent("""
    You are a hiring-focused assistant. Compare a Job Description (JD) with a candidate CV and
    produce a single JSON object (no extra text, no commentary) that assesses fit.

    Requirements for the JSON output (HR focus):
    - Respond ONLY with a single JSON object encoded in UTF-8 and valid JSON.
    - Do not include any explanatory text, markdown, or trailing commas.
    - IMPORTANT: The JSON must strictly adhere to the provided schema below.

    JSON schema (exact keys):
    {{
      "match_score": integer between 0 and 100,
      "summary": string (short, 1-3 sentences describing overall fit),
      "strengths": array of strings (key skills/experience from CV that match JD),
      "missing_requirements": array of strings (important JD requirements not visible in CV),
      "verdict": one of ["strong match", "possible match", "not a match"]
    }}

    Rules to score and produce fields:
    1) Evaluate technical skills, years of experience, domain knowledge, tools, certifications,
       and soft skills required in the JD. Give more weight to mandatory requirements.
    2) match_score: produce an integer 0-100. Use 0-39 = not a match, 40-69 = possible match, 70-100 = strong match.
    3) strengths: up to 6 bullet items pulled verbatim or paraphrased from the CV.
    4) missing_requirements: up to 6 items from the JD not found in the CV; prefer exact phrasing.
    5) verdict: map from match_score using the ranges above.

    Now compare the Job Description and the CV below.

    JOB DESCRIPTION:
    ------------------------------------------------------------
    {jd}
    ------------------------------------------------------------

    {candidate_label} CV:
    ------------------------------------------------------------
    {cv}
    ------------------------------------------------------------

    Produce the JSON now.
    """).format(jd=jd_text, cv=cv_text, candidate_label=candidate_label)
    return instruct


def save_prompt_md(text: str, path: Path):
    """Saglabā prompt tekstu .md failā."""
    path.write_text(text, encoding="utf-8")


def call_gemini(prompt: str) -> dict:
    """Izsauc Gemini Flash 2.5, izmantojot google-genai SDK ar stingru JSON izvades shēmu."""
    
    json_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "match_score": types.Schema(type=types.Type.INTEGER, description="Atbilstības rādītājs no 0 līdz 100."),
            "summary": types.Schema(type=types.Type.STRING, description="Īss kopsavilkums (1-3 teikumi)."),
            "strengths": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
            "missing_requirements": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
            "verdict": types.Schema(type=types.Type.STRING, enum=["strong match", "possible match", "not a match"]),
        },
        required=REQUIRED_KEYS
    )

    config = types.GenerateContentConfig(
        temperature=TEMPERATURE,
        response_mime_type="application/json", 
        response_schema=json_schema, 
    )
    
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=config,
        )
    except Exception as e:
        raise RuntimeError(f"Gemini API zvans neizdevās: {e}")

    json_text = response.text.strip()

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Nevarēja parsēt JSON atbildi no modeļa: {e}. Izejteksts: {json_text}")
        
    return parsed


def validate_hr_json(obj: dict) -> bool:
    """Validē saņemto JSON atbilstoši uzdevuma prasībām."""
    if not isinstance(obj, dict):
        return False
    for k in REQUIRED_KEYS:
        if k not in obj:
            return False
    if not isinstance(obj.get("match_score"), int) or not (0 <= obj.get("match_score") <= 100):
        return False
    if not isinstance(obj.get("summary"), str):
        return False
    if not isinstance(obj.get("strengths"), list):
        return False
    if not isinstance(obj.get("missing_requirements"), list):
        return False
    if obj.get("verdict") not in ["strong match", "possible match", "not a match"]:
        return False
    return True


def generate_report_md(json_obj: dict, candidate_label: str, out_path: Path):
    """Ģenerē īsu pārskatu (Markdown) no JSON atbildes."""
    md = []
    md.append(f"# CV Review — {candidate_label} (Gemini Flash 2.5)\n")
    md.append(f"**Match score:** {json_obj.get('match_score')} / 100  ")
    md.append(f"**Verdict:** {json_obj.get('verdict')}\n")
    md.append("## Summary\n")
    md.append(json_obj.get('summary') + "\n")
    md.append("## Strengths (from CV) / Galvenās prasmes un pieredze\n")
    for s in json_obj.get('strengths', []):
        md.append(f"- {s}")
    md.append("\n## Missing / Not Evident Requirements (from JD) / Trūkstošās prasības\n")
    for m in json_obj.get('missing_requirements', []):
        md.append(f"- {m}")

    out_text = "\n".join(md)
    out_path.write_text(out_text, encoding='utf-8')


def main():
    """Galvenā izpildes loģika, atkārtojot 2.-5. soli visiem CV."""
    try:
        jd_text = read_text(JD_PATH)
    except FileNotFoundError:
        print(f"Kļūda: Darba apraksta fails '{JD_PATH}' (jd.txt) nav atrasts. Lūdzu pārliecinieties, ka 'sample_inputs/jd.txt' pastāv.")
        return

    for i, cv_path in enumerate(CV_PATHS, start=1):
        try:
            cv_text = read_text(cv_path)
        except FileNotFoundError:
            print(f"Kļūda: CV fails '{cv_path}' nav atrasts. Izlaižam šo kandidātu.")
            continue
            
        label = f"Candidate {i}"

        # 2. solis: Sagatavo prompt.md 
        prompt_text = build_prompt(jd_text, cv_text, candidate_label=label)
        prompt_file = PROMPT_DIR / f"prompt_cv{i}.md"
        save_prompt_md(prompt_text, prompt_file)
        print(f"\nSaved prompt for {label} -> {prompt_file}")

        # 3. solis: Izsauc Gemini Flash 2.5 
        print(f"Calling model {MODEL_NAME} for {label}...")
        try:
            model_json = call_gemini(prompt_text)
        except RuntimeError as e:
            print(f"Error while calling model for {label}: {e}")
            continue

        # 4. solis: Saglabā JSON atbildi
        if not validate_hr_json(model_json):
            print(f"Warning: model JSON for {label} failed validation. Saving raw response for inspection.")
            (OUTPUT_DIR / f"cv{i}_raw.json").write_text(json.dumps(model_json, ensure_ascii=False, indent=2), encoding='utf-8')
            continue

        out_json_path = OUTPUT_DIR / f"cv{i}.json"
        out_json_path.write_text(json.dumps(model_json, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"Saved JSON result for {label} -> {out_json_path}")

        # 5. solis: Ģenerē īsu pārskatu
        report_path = OUTPUT_DIR / f"cv{i}_report.md"
        generate_report_md(model_json, label, report_path)
        print(f"Saved report for {label} -> {report_path}")

        time.sleep(1) # Atstājam pauzi starp API zvaniem, lai izvairītos no ātruma ierobežojumiem

if __name__ == '__main__':
    main()