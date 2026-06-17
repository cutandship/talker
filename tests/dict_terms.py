# -*- coding: utf-8 -*-
"""Curated-список канонов для переснимка словаря замен на GigaAM v3.

Высокоценные англоязычные термины/бренды, которые русскоязычный диктор называет
вслух, а STT обязан отдать в каноне (Claude, не «клод»). Сгруппировано — чтобы
видеть охват. Зонд (probe_gigaam.py) синтезирует каждый и снимает реальные v3-
мисхиры; транслитератор добивает то, что зонд не покрыл.

ACRONYMS — особая корзина: короткие буквенные (ИИ, API, СМИ). Часть из них STT
схлопывает/обрезает необратимо (см. зонд: ИИ→«и») — для них словарь бессилен,
помечаем отдельно под STT-биасинг/контекст.
"""
from __future__ import annotations

# ── AI: ассистенты, модели, продукты ─────────────────────────────────────────
AI_PRODUCTS = [
    "Claude", "ChatGPT", "GPT", "Gemini", "Copilot", "Grok", "DeepSeek",
    "Llama", "Mistral", "Midjourney", "Perplexity", "Sora", "DALL-E",
    "Stable Diffusion", "Whisper", "Gemma", "Qwen", "Cursor", "Windsurf",
    "Runway", "ElevenLabs", "Suno", "Replit", "Notebook LM", "Operator",
]

# ── AI-компании / лаборатории ────────────────────────────────────────────────
AI_COMPANIES = [
    "OpenAI", "Anthropic", "DeepMind", "Hugging Face", "Stability AI",
    "Cohere", "xAI", "Scale AI", "Mistral AI", "Together AI",
]

# ── Большие техно-бренды ─────────────────────────────────────────────────────
BIG_TECH = [
    "Microsoft", "Google", "Apple", "Amazon", "Meta", "Tesla", "Samsung",
    "Intel", "AMD", "Qualcomm", "IBM", "Oracle", "Adobe", "Salesforce",
    "Nvidia", "SpaceX", "Netflix", "Spotify", "PayPal", "Uber",
]

# ── Дев-инструменты / сервисы ────────────────────────────────────────────────
DEV_TOOLS = [
    "GitHub", "GitLab", "Git", "Docker", "Kubernetes", "VS Code", "Jira",
    "Jenkins", "Terraform", "Ansible", "Postman", "Figma", "Notion", "Slack",
    "Discord", "Obsidian", "Linear", "Vercel", "Netlify", "npm", "Webpack",
    "Vite", "Confluence", "Trello", "Sentry", "Grafana", "Prometheus",
]

# ── Языки / фреймворки / библиотеки ──────────────────────────────────────────
LANGS_FRAMEWORKS = [
    "Python", "JavaScript", "TypeScript", "React", "Vue", "Angular",
    "Node.js", "Django", "Flask", "FastAPI", "PyTorch", "TensorFlow",
    "Next.js", "Svelte", "Rust", "Golang", "Kotlin", "Swift", "Laravel",
    "Spring", "LangChain", "Pandas", "NumPy", "Tailwind", "GraphQL",
]

# ── БД / облака / инфра ──────────────────────────────────────────────────────
DATA_CLOUD = [
    "PostgreSQL", "MySQL", "MongoDB", "Redis", "SQLite", "Elasticsearch",
    "Kafka", "RabbitMQ", "AWS", "Azure", "GCP", "Cloudflare", "Supabase",
    "Firebase", "Snowflake", "Databricks", "ClickHouse", "Nginx",
]

# ── ОС / платформы ───────────────────────────────────────────────────────────
PLATFORMS = [
    "Windows", "Linux", "Ubuntu", "Debian", "Android", "iOS", "macOS",
]

# ── Потребительские приложения / устройства ──────────────────────────────────
CONSUMER = [
    "YouTube", "Telegram", "WhatsApp", "Instagram", "TikTok", "Zoom",
    "Twitch", "Reddit", "LinkedIn", "iPhone", "iPad", "MacBook", "AirPods",
    "PlayStation", "Xbox", "Gmail", "Chrome", "Safari", "Firefox",
]

# ── Аббревиатуры (особая корзина — часть необратима на STT) ───────────────────
ACRONYMS = [
    "ИИ", "API", "JSON", "HTML", "CSS", "SQL", "URL", "HTTP", "HTTPS", "SDK",
    "CLI", "GPU", "CPU", "RAM", "СМИ", "IT", "ML", "LLM", "UI", "UX", "PDF",
    "USB", "VPN", "DNS", "CRM", "SaaS", "REST",
]

CATEGORIES = {
    "ai_products": AI_PRODUCTS,
    "ai_companies": AI_COMPANIES,
    "big_tech": BIG_TECH,
    "dev_tools": DEV_TOOLS,
    "langs_frameworks": LANGS_FRAMEWORKS,
    "data_cloud": DATA_CLOUD,
    "platforms": PLATFORMS,
    "consumer": CONSUMER,
    "acronyms": ACRONYMS,
}


def all_terms(include_acronyms: bool = True) -> list[str]:
    out: list[str] = []
    seen = set()
    for name, lst in CATEGORIES.items():
        if name == "acronyms" and not include_acronyms:
            continue
        for t in lst:
            if t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    total = sum(len(v) for v in CATEGORIES.values())
    print(f"категорий: {len(CATEGORIES)}, терминов всего: {total}")
    for name, lst in CATEGORIES.items():
        print(f"  {name:18} {len(lst):3}")
    print(f"уникальных (с акронимами): {len(all_terms())}")
