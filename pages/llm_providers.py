"""
LLM Providers — pagina de management pentru toți providerii AI.
Permite vizualizarea, configurarea și switchul providerului activ.
"""
import os
import re
from pathlib import Path
import streamlit as st
from core.llm_router import get_router, reset_router, VALID_PROVIDERS

ENV_PATH = Path(__file__).parent.parent / ".env"

# ── Metadata per provider ──────────────────────────────────────────────────────

PROVIDER_INFO = {
    "anthropic": {
        "label":         "Anthropic Claude",
        "icon":          "🧠",
        "env_key":       "ANTHROPIC_API_KEY",
        "env_model":     "ANTHROPIC_MODEL",
        "default_model": "claude-haiku-4-5-20251001",
        "models":        ["claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6"],
        "key_label":     "API Key",
        "key_placeholder": "sk-ant-...",
        "key_type":      "password",
        "docs_url":      "https://console.anthropic.com",
        "docs_label":    "console.anthropic.com",
        "type":          "api",
    },
    "ollama": {
        "label":         "Ollama (local)",
        "icon":          "🦙",
        "env_key":       "OLLAMA_BASE_URL",
        "env_model":     "OLLAMA_MODEL",
        "default_model": "qwen2.5:14b",
        "models":        ["qwen2.5:14b", "llama3.2:3b", "mistral:7b", "gemma3:12b"],
        "key_label":     "Base URL",
        "key_placeholder": "http://localhost:11434",
        "key_type":      "default",
        "docs_url":      "https://ollama.ai",
        "docs_label":    "ollama.ai",
        "type":          "local",
    },
    "gemini": {
        "label":         "Google Gemini",
        "icon":          "✨",
        "env_key":       "GEMINI_API_KEY",
        "env_model":     "GEMINI_MODEL",
        "default_model": "gemini-2.0-flash",
        "models":        ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"],
        "key_label":     "API Key",
        "key_placeholder": "AIza...",
        "key_type":      "password",
        "docs_url":      "https://aistudio.google.com",
        "docs_label":    "aistudio.google.com",
        "type":          "api",
    },
    "groq": {
        "label":         "Groq",
        "icon":          "⚡",
        "env_key":       "GROQ_API_KEY",
        "env_model":     "GROQ_MODEL",
        "default_model": "llama-3.3-70b-versatile",
        "models":        ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
        "key_label":     "API Key",
        "key_placeholder": "gsk_...",
        "key_type":      "password",
        "docs_url":      "https://console.groq.com",
        "docs_label":    "console.groq.com",
        "type":          "api",
    },
    "mistral": {
        "label":         "Mistral AI",
        "icon":          "💫",
        "env_key":       "MISTRAL_API_KEY",
        "env_model":     "MISTRAL_MODEL",
        "default_model": "mistral-small-latest",
        "models":        ["mistral-small-latest", "mistral-medium-latest", "mistral-large-latest"],
        "key_label":     "API Key",
        "key_placeholder": "...",
        "key_type":      "password",
        "docs_url":      "https://console.mistral.ai",
        "docs_label":    "console.mistral.ai",
        "type":          "api",
    },
}


# ── .env helpers ───────────────────────────────────────────────────────────────

def _read_env() -> str:
    if ENV_PATH.exists():
        return ENV_PATH.read_text(encoding="utf-8")
    return ""


def _write_env_key(key: str, value: str) -> None:
    """Setează sau actualizează o variabilă în .env (în-place)."""
    content = _read_env()
    # Dacă există (cu sau fără #), înlocuiește
    pattern = re.compile(rf"^#?\s*{re.escape(key)}\s*=.*$", re.MULTILINE)
    new_line = f"{key}={value}"
    if pattern.search(content):
        content = pattern.sub(new_line, content)
    else:
        # Adaugă la sfârșit
        content = content.rstrip("\n") + f"\n{new_line}\n"
    ENV_PATH.write_text(content, encoding="utf-8")


def _get_env_value(key: str) -> str:
    """Citește valoarea unui key din .env (ignoră liniile comentate)."""
    content = _read_env()
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#"):
            continue
        if "=" in line and line.split("=", 1)[0].strip() == key:
            return line.split("=", 1)[1].strip()
    return ""


def _active_provider() -> str:
    return (os.getenv("ACTIVE_PROVIDER") or _get_env_value("ACTIVE_PROVIDER") or "anthropic").lower()


def _is_key_set(info: dict) -> bool:
    """Returnează True dacă key/URL-ul providerului este configurat."""
    val = os.getenv(info["env_key"]) or _get_env_value(info["env_key"])
    if not val:
        return False
    if info["type"] == "local":
        return True  # URL-ul local e mereu "setat" dacă e prezent
    return bool(val and val not in ("sk-ant-your-key-here", "your-key-here"))


# ── Render ─────────────────────────────────────────────────────────────────────

def render():
    st.title("🤖 LLM Providers")
    st.markdown("Gestionează și configurează toți providerii AI disponibili. Schimbările se salvează în fișierul `.env` și se aplică imediat.")
    st.markdown("---")

    active = _active_provider()

    # ── Status bar ─────────────────────────────────────────────────────────────
    try:
        router = get_router()
        current_provider = router.provider_name
        st.markdown(
            f"<div style='background:#1a2e1a;border-left:5px solid #22c55e;padding:10px 16px;"
            f"border-radius:4px;margin-bottom:16px'>"
            f"<span style='color:#86efac;font-size:12px;font-weight:600;letter-spacing:0.5px'>PROVIDER ACTIV</span><br>"
            f"<span style='color:#ffffff;font-size:18px;font-weight:700'>"
            f"{PROVIDER_INFO.get(current_provider, {}).get('icon','🤖')} "
            f"{PROVIDER_INFO.get(current_provider, {}).get('label', current_provider.upper())}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception as e:
        st.markdown(
            f"<div style='background:#2e1a1a;border-left:5px solid #ef4444;padding:10px 16px;"
            f"border-radius:4px;margin-bottom:16px'>"
            f"<span style='color:#fca5a5;font-size:12px;font-weight:600'>PROVIDER INACTIV</span><br>"
            f"<span style='color:#ffffff;font-size:14px'>Eroare la inițializare: {e}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        current_provider = None

    # ── Provider cards ─────────────────────────────────────────────────────────
    for pname in VALID_PROVIDERS:
        info = PROVIDER_INFO[pname]
        is_active = (pname == active)
        key_set = _is_key_set(info)

        # Culori card
        border_color = "#22c55e" if is_active else ("#3b82f6" if key_set else "#2d3250")
        bg_color = "#0f2318" if is_active else "#1e2130"

        st.markdown(
            f"<div style='background:{bg_color};border:1px solid {border_color};"
            f"border-radius:8px;padding:14px 18px;margin-bottom:4px'>"
            f"<span style='font-size:18px'>{info['icon']}</span> "
            f"<span style='font-size:16px;font-weight:700;color:#f1f5f9'>{info['label']}</span>"
            + (f"&nbsp;&nbsp;<span style='background:#16a34a33;color:#22c55e;font-size:11px;"
               f"font-weight:700;padding:2px 8px;border-radius:10px'>ACTIV</span>" if is_active else "")
            + (f"&nbsp;&nbsp;<span style='background:#1e3a5f;color:#60a5fa;font-size:11px;"
               f"font-weight:600;padding:2px 8px;border-radius:10px'>Configurat</span>" if key_set and not is_active else "")
            + (f"&nbsp;&nbsp;<span style='background:#1c1c1c;color:#94a3b8;font-size:11px;"
               f"padding:2px 8px;border-radius:10px'>Neconfigurat</span>" if not key_set and not is_active else "")
            + "</div>",
            unsafe_allow_html=True,
        )

        with st.expander(f"Configurează {info['label']}", expanded=is_active):
            col_key, col_model = st.columns([3, 2])

            with col_key:
                current_val = os.getenv(info["env_key"]) or _get_env_value(info["env_key"]) or ""
                if info["key_type"] == "password":
                    new_val = st.text_input(
                        info["key_label"],
                        value=current_val,
                        type="password",
                        placeholder=info["key_placeholder"],
                        key=f"val_{pname}",
                        help=f"Obține cheia de pe {info['docs_label']}",
                    )
                else:
                    new_val = st.text_input(
                        info["key_label"],
                        value=current_val or info["key_placeholder"],
                        placeholder=info["key_placeholder"],
                        key=f"val_{pname}",
                    )

            with col_model:
                current_model = os.getenv(info["env_model"]) or _get_env_value(info["env_model"]) or info["default_model"]
                # Dacă modelul curent nu e în lista predefinită, îl adăugăm
                model_options = info["models"]
                if current_model not in model_options:
                    model_options = [current_model] + model_options
                model_idx = model_options.index(current_model) if current_model in model_options else 0
                new_model = st.selectbox(
                    "Model",
                    model_options,
                    index=model_idx,
                    key=f"model_{pname}",
                )

            col_save, col_activate, col_test = st.columns([2, 2, 2])

            with col_save:
                if st.button("💾 Salvează", key=f"save_{pname}", use_container_width=True):
                    if new_val.strip():
                        _write_env_key(info["env_key"], new_val.strip())
                        os.environ[info["env_key"]] = new_val.strip()
                    if new_model:
                        _write_env_key(info["env_model"], new_model)
                        os.environ[info["env_model"]] = new_model
                    reset_router()
                    st.success("✅ Salvat!")
                    st.rerun()

            with col_activate:
                if not is_active:
                    if st.button(f"🔄 Activează", key=f"activate_{pname}", use_container_width=True,
                                 type="primary"):
                        if not key_set and not new_val.strip():
                            st.error(f"Configurează {info['key_label']} mai întâi.")
                        else:
                            if new_val.strip():
                                _write_env_key(info["env_key"], new_val.strip())
                                os.environ[info["env_key"]] = new_val.strip()
                            _write_env_key("ACTIVE_PROVIDER", pname)
                            os.environ["ACTIVE_PROVIDER"] = pname
                            reset_router()
                            st.success(f"✅ Provider schimbat la {info['label']}!")
                            st.rerun()
                else:
                    st.markdown("<div style='padding:8px 0;color:#86efac;font-size:13px;text-align:center'>✓ Provider activ</div>",
                                unsafe_allow_html=True)

            with col_test:
                if st.button("🧪 Testează", key=f"test_{pname}", use_container_width=True):
                    # Aplică temporar valorile dacă sunt neschimbate în env
                    if new_val.strip():
                        os.environ[info["env_key"]] = new_val.strip()
                    if new_model:
                        os.environ[info["env_model"]] = new_model
                    # Forțăm un router cu providerul specificat
                    with st.spinner(f"Se testează {info['label']}..."):
                        try:
                            from core.llm_router import LLMRouter
                            test_router = LLMRouter(pname)
                            test_router.complete("ping", 10)
                            st.success(f"✅ Conexiune OK — {info['label']}")
                        except Exception as e:
                            st.error(f"❌ {e}")

            if info["type"] == "api":
                st.caption(f"🔗 Obține API key: {info['docs_label']}")
            else:
                st.caption("💡 Ollama trebuie să ruleze local: `ollama serve` în terminal")

        st.markdown("")  # spacer

    # ── Info box ───────────────────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("ℹ️ Cum funcționează switchul de provider"):
        st.markdown("""
**Schimbarea providerului** se poate face în 3 moduri:

1. **Din această pagină** — apasă „Activează" pe providerul dorit. Se salvează în `.env` și se aplică imediat.

2. **Din `.env`** — editează manual variabila:
   ```
   ACTIVE_PROVIDER=groq   # anthropic | ollama | gemini | groq | mistral
   ```
   Necesită restart aplicație.

3. **Runtime (din cod)** — fără restart:
   ```python
   from core.llm_router import switch_provider
   switch_provider("groq")
   ```

**Note:**
- Toate modelele suportă același format de prompt — nu e nevoie să modifici alt cod.
- Cache-ul AI rămâne valabil indiferent de provider.
- Ollama rulează local și nu necesită API key — necesită `ollama serve` activ.
        """)
