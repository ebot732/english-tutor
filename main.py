# === ФРАГМЕНТ 1 из 6 ===
import os
import re
import json
import time
import wave
import base64
import sqlite3
import tempfile
import asyncio
import traceback
import urllib.request
import urllib.parse
import urllib.error
import tomllib
import httpx
import flet as ft
import flet_audio as fta
import flet_audio_recorder as far

STORAGE_DIR = os.getenv("FLET_APP_STORAGE_DATA") or tempfile.gettempdir()
DB_PATH = os.path.join(STORAGE_DIR, "tutor_data.db")


def get_app_version() -> str:
    try:
        toml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyproject.toml")
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        ver = data.get("project", {}).get("version", "1.5.0")
        build = data.get("tool", {}).get("flet", {}).get("build_number", 1)
        return f"{ver} (сборка {build})"
    except Exception:
        return "1.5.0"


def get_provider_key_name(provider: str) -> str:
    p = (provider or "").strip()
    if p == "ProxyAPI":
        return "api_key_proxyapi"
    elif p == "VseGPT":
        return "api_key_vsegpt"
    return "api_key_openrouter"


def _clean_tuple_str(val: str) -> str:
    s = (val or "").strip()
    if s.startswith("('") and s.endswith("',)"):
        return s[2:-3]
    if s.startswith('("') and s.endswith('",)'):
        return s[2:-3]
    return s


def init_db():
    os.makedirs(STORAGE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT,
            content TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dictionary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT UNIQUE,
            translation TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    defaults = {
        "provider": "ProxyAPI",
        "api_key_openrouter": "",
        "api_key_proxyapi": "",
        "api_key_vsegpt": "",
        "level": "A1-A2 (Начальный)",
        "topic": "Путешествия и кафе",
        "auto_tts": "0",
        "tts_mode": "Бесплатный голос (Google TTS)",
        "stt_mode": "Бесплатный (Google Speech)"
    }
    for k, v in defaults.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    cursor.execute("SELECT key, value FROM settings")
    for k, v in cursor.fetchall():
        cleaned = _clean_tuple_str(str(v))
        if cleaned != str(v):
            cursor.execute("UPDATE settings SET value = ? WHERE key = ?", (cleaned, k))

    cursor.execute("UPDATE settings SET value = 'Бесплатный голос (Google TTS)' WHERE key = 'tts_mode' AND value = 'Голос телефона (Android TTS)'")
    cursor.execute("UPDATE settings SET value = 'Бесплатный (Google Speech)' WHERE key = 'stt_mode' AND value = 'Средствами телефона (Android)'")

    cursor.execute("SELECT value FROM settings WHERE key = 'api_key'")
    old_row = cursor.fetchone()
    if old_row and old_row:
        cursor.execute("SELECT value FROM settings WHERE key = 'provider'")
        prov_row = cursor.fetchone()
        target_key = get_provider_key_name(prov_row if prov_row else "")
        cursor.execute("UPDATE settings SET value = ? WHERE key = ? AND value = ''", (old_row, target_key))
        cursor.execute("DELETE FROM settings WHERE key = 'api_key'")

    conn.commit()
    conn.close()
# === ФРАГМЕНТ 2 из 6 ===
def get_setting(key: str) -> str:
    if key == "api_key":
        key = get_provider_key_name(get_setting("provider"))
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return _clean_tuple_str(str(row)) if row and row is not None else ""


def set_setting(key: str, value: str):
    if key == "api_key":
        key = get_provider_key_name(get_setting("provider"))
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


def get_ai_config() -> tuple[str, str, str]:
    provider = get_setting("provider").strip()
    api_key = get_setting(get_provider_key_name(provider)).strip()

    if provider == "ProxyAPI":
        url = urllib.parse.urlunparse(("https", "api.proxyapi.ru/v1", "/openai", "", "", ""))
        return url, api_key, "gpt-4o-mini"
    elif provider == "VseGPT":
        url = urllib.parse.urlunparse(("https", "api.vsegpt.ru/v1", "", "", "", ""))
        return url, api_key, "openai/gpt-4o-mini"
    else:
        url = urllib.parse.urlunparse(("https", "openrouter.ai/api/v1", "", "", "", ""))
        return url, api_key, "openrouter/free"


async def ask_llm(messages: list[dict], temperature: float = 0.7) -> str:
    base_url, api_key, model = get_ai_config()
    url = f"{base_url}/chat/completions"
    referer_url = urllib.parse.urlunparse(("https", "github.com", "", "", "", ""))
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": referer_url,
        "X-Title": "EnglishTutorApp",
        "User-Agent": "EnglishTutorApp/1.0"
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature
    }

    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        try:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise RuntimeError(str(data["error"]))
            return data["choices"]["message"]["content"].strip()
        except httpx.HTTPStatusError as e:
            err_body = e.response.text
            raise RuntimeError(f"Ошибка сервера {e.response.status_code} ({base_url}): {err_body[:200]}")
        except httpx.RequestError as e:
            raise RuntimeError(f"Ошибка сети ({base_url}): {e}")


async def _recognize_google_free_wav(wav_path: str) -> str:
    with wave.open(wav_path, "rb") as wf:
        sample_rate = wf.getframerate()
        pcm_data = wf.readframes(wf.getnframes())

    if len(pcm_data) < 1000:
        raise RuntimeError("Запись слишком короткая")

    query = urllib.parse.urlencode({
        "output": "json",
        "lang": "en-US",
        "key": "AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqQaGw",
    })
    url = urllib.parse.urlunparse(("https", "://google.com", "/speech-api/v2/recognize", "", query, ""))
    headers = {
        "Content-Type": f"audio/l16; rate={sample_rate};",
        "User-Agent": "Mozilla/5.0",
    }

    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.post(url, headers=headers, content=pcm_data)
        resp.raise_for_status()
        for line in resp.text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                results = obj.get("result", [])
                if results and results.get("alternative"):
                    return results["alternative"].get("transcript", "").strip()
            except Exception:
                continue
    return ""


async def recognize_audio_async(file_path: str) -> str:
    if not os.path.exists(file_path) or os.path.getsize(file_path) < 100:
        raise RuntimeError("Файл записи пуст или не создан")

    stt_mode = get_setting("stt_mode").strip() or "Бесплатный (Google Speech)"

    if stt_mode == "Бесплатный (Google Speech)":
        return await _recognize_google_free_wav(file_path)

    provider = get_setting("provider").strip()
    base_url, api_key, _ = get_ai_config()

    if provider in ("ProxyAPI", "VseGPT") and api_key:
        url = f"{base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {api_key}"}
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            with open(file_path, "rb") as audio_file:
                files = {"file": (os.path.basename(file_path), audio_file, "audio/wav")}
                data = {"model": "whisper-1", "language": "en"}
                response = await client.post(url, headers=headers, data=data, files=files)
                if response.status_code == 401:
                    raise RuntimeError("Неверный API-ключ для Whisper. Проверьте ключ в Настройках.")
                if response.status_code >= 400:
                    raise RuntimeError(f"Ошибка Whisper ({response.status_code}): {response.text[:180]}")
                res_json = response.json()
                return (res_json.get("text") or "").strip()

    raise RuntimeError("Для распознавания через ИИ нужен ключ ProxyAPI или VseGPT в Настройках.")
# === ФРАГМЕНТ 3 из 6 ===
async def get_tts_bytes_async(clean_text: str, use_ai: bool = False) -> bytes:
    normalized = " ".join(clean_text.split())[:400]
    base_url, api_key, _ = get_ai_config()
    provider = get_setting("provider").strip()
    last_err = "Нет доступного сервиса озвучки"

    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        if use_ai and provider in ("ProxyAPI", "VseGPT") and api_key:
            try:
                url = f"{base_url}/audio/speech"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": "tts-1",
                    "input": normalized,
                    "voice": "nova",
                    "response_format": "mp3"
                }
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 200 and len(resp.content) > 500:
                    return resp.content
                last_err = f"TTS API ({resp.status_code}): {resp.text[:150]}"
            except Exception as e:
                last_err = f"TTS API: {e}"

        free_urls = [
            urllib.parse.urlunparse((
                "https",
                "://google.com",
                "/translate_tts",
                "",
                urllib.parse.urlencode({"ie": "UTF-8", "q": normalized[:200], "tl": "en", "client": "gtx"}),
                "",
            )),
            urllib.parse.urlunparse((
                "https",
                "://streamelements.com",
                "/kappa/v2/speech",
                "",
                urllib.parse.urlencode({"voice": "Brian", "text": normalized[:200]}),
                "",
            )),
        ]
        for url in free_urls:
            try:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200 and len(resp.content) > 500 and not resp.content.lstrip().startswith(b"<"):
                    return resp.content
            except Exception as e:
                last_err = str(e)

    raise RuntimeError(last_err)


def build_system_prompt() -> str:
    level = get_setting("level")
    topic = get_setting("topic")
    return (
        f"Ты дружелюбный репетитор английского языка. Уровень ученика: {level}.\n"
        f"Веди с ним простой диалог на выбранную тему: {topic}.\n"
        "Правила:\n"
        "1. Пиши короткие и простые фразы на английском (1-2 предложения), используй базовую лексику.\n"
        "2. После своей реплики на английском, с новой строки в скобках дай её перевод на русский.\n"
        "   ВАЖНО: Если в своей реплике ты используешь новое для ученика слово (не из базовой лексики), "
        "выдели его двойными звёздочками, например: **delicious**.\n"
        "3. Если ученик допустил ошибку в грамматике, выборе слов или правописании, "
        "сначала мягко поправь его на русском языке, объясни как правильно, а затем продолжи диалог.\n"
        "4. Всегда заканчивай свой ответ простым вопросом, чтобы ученик мог продолжить разговор."
    )


def extract_english_part(text: str) -> str:
    cleaned = re.sub(r'\(.*?\)', '', text, flags=re.DOTALL)
    cleaned = cleaned.replace("**", "").strip()
    return cleaned


async def main(page: ft.Page):
    try:
        init_db()

        page.title = "English AI Tutor"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.padding = 0
        page.bgcolor = "#F4F6F9"

        def show_snack(msg: str, color: str = ft.Colors.BLUE_700, duration: int = 3000):
            sb = ft.SnackBar(
                ft.Text(msg, color=ft.Colors.WHITE),
                bgcolor=color,
                duration=duration,
            )
            page.overlay.append(sb)
            sb.open = True
            page.update()

        chat_list = ft.ListView(expand=True, spacing=12, padding=15, auto_scroll=True)

        recorder = far.AudioRecorder(
            configuration=far.AudioRecorderConfiguration(
                encoder=far.AudioEncoder.WAV,
                sample_rate=16000,
                num_channels=1,
            )
        )
        page.services.append(recorder)
        page.update()

        is_recording = False
        temp_audio_path = os.path.join(STORAGE_DIR, "temp_recording.wav")

        mic_button = ft.IconButton(
            icon=ft.Icons.MIC_ROUNDED,
            icon_color=ft.Colors.BLUE_600,
            tooltip="Голосовой ввод (нажмите для старта, ещё раз — для остановки)",
        )
# === ФРАГМЕНТ 4 из 6 ===
        async def mic_button_click(e):
            nonlocal is_recording
            if is_recording:
                try:
                    recorded_path = await recorder.stop_recording()
                    is_recording = False
                    mic_button.icon = ft.Icons.MIC_ROUNDED
                    mic_button.icon_color = ft.Colors.BLUE_600
                    loading_indicator.visible = True
                    page.update()
                    show_snack("Распознаю речь...", ft.Colors.BLUE_700, duration=1200)

                    actual_path = recorded_path if (recorded_path and os.path.exists(recorded_path)) else temp_audio_path
                    text = await recognize_audio_async(actual_path)

                    loading_indicator.visible = False
                    if text:
                        user_input.value = text
                        page.update()
                        await send_message()
                    else:
                        page.update()
                        show_snack("Речь не распознана. Попробуйте говорить четче.", ft.Colors.ORANGE_700, duration=1500)
                except Exception as ex:
                    is_recording = False
                    loading_indicator.visible = False
                    mic_button.icon = ft.Icons.MIC_ROUNDED
                    mic_button.icon_color = ft.Colors.BLUE_600
                    page.update()
                    chat_list.controls.append(build_message_bubble("error", f"🎤 Ошибка распознавания: {ex}"))
                    page.update()
            else:
                try:
                    has_perm = await recorder.has_permission()
                    if not has_perm:
                        show_snack("Выдайте разрешение на микрофон и нажмите кнопку снова", ft.Colors.ORANGE_700, duration=2000)
                        return

                    if os.path.exists(temp_audio_path):
                        try:
                            os.remove(temp_audio_path)
                        except Exception:
                            pass

                    await recorder.start_recording(output_path=temp_audio_path)

                    is_recording = True
                    mic_button.icon = ft.Icons.STOP_CIRCLE_ROUNDED
                    mic_button.icon_color = ft.Colors.RED_400
                    page.update()
                    show_snack("Идет запись... Нажмите красную кнопку для отправки.", ft.Colors.GREEN_700, duration=800)
                except Exception as ex:
                    is_recording = False
                    mic_button.icon = ft.Icons.MIC_ROUNDED
                    mic_button.icon_color = ft.Colors.BLUE_600
                    page.update()
                    show_snack(f"Ошибка микрофона: {ex}", ft.Colors.RED_600, duration=2000)

        mic_button.on_click = lambda e: page.run_task(mic_button_click, e)

        async def speak_text(english_text: str):
            clean_text = extract_english_part(english_text)
            if not clean_text:
                return

            tts_mode = get_setting("tts_mode").strip() or "Бесплатный голос (Google TTS)"

            try:
                use_ai = (tts_mode == "Голос ИИ (OpenAI TTS)")
                audio_bytes = await get_tts_bytes_async(clean_text, use_ai=use_ai)

                tts_path = os.path.join(STORAGE_DIR, f"tts_{int(time.time() * 1000)}.mp3")
                with open(tts_path, "wb") as f:
                    f.write(audio_bytes)

                for fname in os.listdir(STORAGE_DIR):
                    if fname.startswith("tts_") and fname.endswith(".mp3") and os.path.join(STORAGE_DIR, fname) != tts_path:
                        try:
                            os.remove(os.path.join(STORAGE_DIR, fname))
                        except Exception:
                            pass

                for item in list(page.services):
                    if isinstance(item, fta.Audio):
                        page.services.remove(item)

                player = fta.Audio(src=tts_path, autoplay=True, playback_rate=0.9)
                page.services.append(player)
                page.update()
            except Exception as e:
                chat_list.controls.append(
                    build_message_bubble("error", f"🔊 Ошибка озвучки: {e}")
                )
                page.update()

        user_input = ft.TextField(
            hint_text="Напишите или надиктуйте на EN...",
            expand=True,
            border_radius=20,
            bgcolor=ft.Colors.WHITE,
            content_padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            on_submit=lambda e: page.run_task(send_message)
        )
        loading_indicator = ft.ProgressBar(visible=False, color=ft.Colors.BLUE_600)

        async def save_word_to_dict(word: str):
            word_clean = re.sub(r'[^\w\s-]', '', word).strip().lower()
            if not word_clean:
                return
            show_snack(f"Перевожу и сохраняю «{word_clean}»...")
            try:
                translation = await ask_llm([{
                    "role": "user",
                    "content": f"Дай только перевод на русский язык для английского слова '{word_clean}' (1-2 слова, без лишнего текста)."
                }], temperature=0.3)
            except Exception:
                translation = "Перевод не получен"

            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO dictionary (word, translation) VALUES (?, ?)",
                (word_clean, translation)
            )
            conn.commit()
            conn.close()
            show_snack(f"Сохранено: {word_clean} — {translation}", ft.Colors.GREEN_700)
            load_dictionary()
# === ФРАГМЕНТ 5 из 6 ===
        def build_message_bubble(role: str, content: str):
            is_user = (role == "user")
            is_error = (role == "error")
            new_words = re.findall(r'\*\*(.*?)\*\*', content) if (not is_user and not is_error) else []

            word_chips = []
            for w in set(new_words):
                word_chips.append(
                    ft.Button(
                        content=f"+ {w}",
                        icon=ft.Icons.BOOKMARK_ADD_OUTLINED,
                        style=ft.ButtonStyle(
                            padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                            bgcolor=ft.Colors.AMBER_100,
                            color="#DD000000",
                        ),
                        on_click=lambda e, word=w: page.run_task(save_word_to_dict, word)
                    )
                )

            bubble_controls = [
                ft.Text(
                    content.replace("**", ""),
                    color=ft.Colors.WHITE if (is_user or is_error) else "#DD000000",
                    size=15,
                    selectable=True
                )
            ]

            if not is_user and not is_error:
                bubble_controls.append(
                    ft.Row(
                        [
                            ft.IconButton(
                                icon=ft.Icons.VOLUME_UP_ROUNDED,
                                icon_color=ft.Colors.BLUE_700,
                                tooltip="Прослушать",
                                on_click=lambda e, txt=content: page.run_task(speak_text, txt)
                            ),
                            *word_chips
                        ],
                        wrap=True,
                        spacing=5
                    )
                )

            bg = ft.Colors.BLUE_600 if is_user else (ft.Colors.RED_600 if is_error else ft.Colors.WHITE)

            return ft.Row(
                [
                    ft.Container(
                        content=ft.Column(bubble_controls, spacing=6),
                        bgcolor=bg,
                        padding=14,
                        border_radius=16,
                        width=page.width * 0.82 if page.width else 300,
                        shadow=ft.BoxShadow(blur_radius=4, color="#1F000000")
                    )
                ],
                alignment=ft.MainAxisAlignment.END if is_user else ft.MainAxisAlignment.START
            )

        def load_chat_history():
            chat_list.controls.clear()
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT role, content FROM messages")
            rows = cursor.fetchall()
            conn.close()
            for r, c in rows:
                chat_list.controls.append(build_message_bubble(r, c))

        async def send_message():
            text = (user_input.value or "").strip()
            if not text:
                return

            api_key = get_setting("api_key").strip()
            if not api_key:
                chat_list.controls.append(
                    build_message_bubble("error", "⚠️ Сначала вставьте API-ключ во вкладке «Настройки» и нажмите «Сохранить настройки»!")
                )
                page.update()
                return

            user_input.value = ""
            loading_indicator.visible = True
            chat_list.controls.append(build_message_bubble("user", text))
            page.update()

            try:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("INSERT INTO messages (role, content) VALUES (?, ?)", ("user", text))
                conn.commit()

                cursor.execute("SELECT role, content FROM messages ORDER BY id DESC LIMIT 12")
                history = [{"role": r, "content": c} for r, c in reversed(cursor.fetchall())]
                conn.close()

                messages_payload = [{"role": "system", "content": build_system_prompt()}] + history
                ai_reply = await ask_llm(messages_payload, temperature=0.7)

                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("INSERT INTO messages (role, content) VALUES (?, ?)", ("assistant", ai_reply))
                conn.commit()
                conn.close()

                chat_list.controls.append(build_message_bubble("assistant", ai_reply))
                loading_indicator.visible = False
                page.update()

                if get_setting("auto_tts") == "1":
                    await asyncio.sleep(0.1)
                    await speak_text(ai_reply)

            except Exception as ex:
                loading_indicator.visible = False
                chat_list.controls.append(build_message_bubble("error", f"Ошибка ИИ: {ex}"))
                page.update()
# === ФРАГМЕНТ 6 из 7 ===
        def clear_chat(e):
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages")
            conn.commit()
            conn.close()
            chat_list.controls.clear()
            page.update()
            show_snack("История диалога очищена")

        chat_view = ft.Column(
            [
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Text("Чат с репетитором", size=18, weight=ft.FontWeight.BOLD),
                            ft.IconButton(
                                icon=ft.Icons.DELETE_SWEEP_OUTLINED,
                                tooltip="Начать диалог заново",
                                on_click=clear_chat
                            )
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                    ),
                    padding=ft.Padding.symmetric(horizontal=16, vertical=8),
                    bgcolor=ft.Colors.WHITE
                ),
                loading_indicator,
                chat_list,
                ft.Container(
                    content=ft.Row(
                        [
                            mic_button,
                            user_input,
                            ft.IconButton(
                                icon=ft.Icons.SEND_ROUNDED,
                                icon_color=ft.Colors.BLUE_600,
                                on_click=lambda e: page.run_task(send_message)
                            )
                        ],
                        spacing=4
                    ),
                    padding=10,
                    bgcolor=ft.Colors.WHITE
                )
            ],
            expand=True,
            spacing=0
        )

        dict_list = ft.ListView(expand=True, spacing=8, padding=15)

        def delete_word(word_id: int):
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM dictionary WHERE id = ?", (word_id,))
            conn.commit()
            conn.close()
            load_dictionary()

        def load_dictionary():
            dict_list.controls.clear()
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id, word, translation FROM dictionary ORDER BY id DESC")
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                dict_list.controls.append(
                    ft.Container(
                        content=ft.Text(
                            "Словарь пока пуст.\nНажимайте на кнопки «+ слово» под ответами репетитора, чтобы сохранять новые слова!",
                            text_align=ft.TextAlign.CENTER,
                            color=ft.Colors.GREY_600
                        ),
                        padding=30
                    )
                )
            else:
                for wid, word, trans in rows:
                    dict_list.controls.append(
                        ft.Card(
                            content=ft.ListTile(
                                leading=ft.IconButton(
                                    icon=ft.Icons.VOLUME_UP,
                                    icon_color=ft.Colors.BLUE_600,
                                    on_click=lambda e, w=word: page.run_task(speak_text, w)
                                ),
                                title=ft.Text(word, weight=ft.FontWeight.BOLD, size=16),
                                subtitle=ft.Text(trans, size=14),
                                trailing=ft.IconButton(
                                    icon=ft.Icons.DELETE_OUTLINE,
                                    icon_color=ft.Colors.RED_400,
                                    on_click=lambda e, i=wid: delete_word(i)
                                )
                            )
                        )
                    )
            page.update()

        dict_view = ft.Column(
            [
                ft.Container(
                    content=ft.Text("Мой словарь", size=18, weight=ft.FontWeight.BOLD),
                    padding=ft.Padding.symmetric(horizontal=16, vertical=14),
                    bgcolor=ft.Colors.WHITE,
                    width=float("inf")
                ),
                dict_list
            ],
            expand=True,
            spacing=0,
            visible=False
        )
# === ФРАГМЕНТ 7 из 7 ===
        initial_provider = get_setting("provider") or "ProxyAPI"

        api_key_tf = ft.TextField(
            label=f"API-ключ для {initial_provider}",
            value=get_setting(get_provider_key_name(initial_provider)),
            password=True,
            can_reveal_password=True,
            hint_text="Вставьте ключ для выбранного провайдера"
        )

        def on_provider_change(e):
            selected = provider_dd.value or "ProxyAPI"
            api_key_tf.label = f"API-ключ для {selected}"
            api_key_tf.value = get_setting(get_provider_key_name(selected))
            save_status_text.value = ""
            page.update()

        provider_dd = ft.Dropdown(
            label="Провайдер ИИ",
            value=initial_provider,
            on_select=on_provider_change,
            options=[
                ft.dropdown.Option("ProxyAPI"),
                ft.dropdown.Option("VseGPT"),
                ft.dropdown.Option("OpenRouter (Бесплатно)"),
            ]
        )

        level_dd = ft.Dropdown(
            label="Ваш уровень английского",
            value=get_setting("level") or "A1-A2 (Начальный)",
            options=[
                ft.dropdown.Option("A1-A2 (Начальный)"),
                ft.dropdown.Option("B1-B2 (Средний)"),
                ft.dropdown.Option("C1 (Продвинутый)"),
            ]
        )

        topic_tf = ft.TextField(
            label="Тема диалога",
            value=get_setting("topic") or "Путешествия и кафе",
            hint_text="Например: Заказ еды, Собеседование, Хобби"
        )

        tts_mode_dd = ft.Dropdown(
            label="Тип озвучки учителя",
            value=get_setting("tts_mode") or "Бесплатный голос (Google TTS)",
            options=[
                ft.dropdown.Option("Бесплатный голос (Google TTS)"),
                ft.dropdown.Option("Голос ИИ (OpenAI TTS)"),
            ]
        )

        stt_mode_dd = ft.Dropdown(
            label="Распознавание голоса (микрофон)",
            value=get_setting("stt_mode") or "Бесплатный (Google Speech)",
            options=[
                ft.dropdown.Option("Бесплатный (Google Speech)"),
                ft.dropdown.Option("ИИ (Whisper API)"),
            ]
        )

        auto_tts_sw = ft.Switch(
            label="Автоматически озвучивать ответы учителя",
            value=(get_setting("auto_tts") == "1")
        )

        save_status_text = ft.Text("", color=ft.Colors.GREEN_700, weight=ft.FontWeight.BOLD)

        def save_settings_click(e):
            try:
                selected_provider = provider_dd.value or "ProxyAPI"
                set_setting("provider", selected_provider)
                set_setting(get_provider_key_name(selected_provider), api_key_tf.value or "")
                set_setting("level", level_dd.value or "A1-A2 (Начальный)")
                set_setting("topic", topic_tf.value or "Путешествия и кафе")
                set_setting("tts_mode", tts_mode_dd.value or "Бесплатный голос (Google TTS)")
                set_setting("stt_mode", stt_mode_dd.value or "Бесплатный (Google Speech)")
                set_setting("auto_tts", "1" if auto_tts_sw.value else "0")
                save_status_text.value = f"✅ Настройки и ключ для {selected_provider} сохранены!"
                save_status_text.color = ft.Colors.GREEN_700
                page.update()
                show_snack("Настройки успешно сохранены!", ft.Colors.GREEN_700, duration=1500)
            except Exception as ex:
                save_status_text.value = f"❌ Ошибка сохранения: {ex}"
                save_status_text.color = ft.Colors.RED_600
                page.update()

        settings_view = ft.Column(
            [
                ft.Container(
                    content=ft.Text("Настройки обучения и ИИ", size=18, weight=ft.FontWeight.BOLD),
                    padding=ft.Padding.symmetric(horizontal=16, vertical=14),
                    bgcolor=ft.Colors.WHITE,
                    width=float("inf")
                ),
                ft.Container(
                    padding=16,
                    content=ft.Column(
                        [
                            provider_dd,
                            api_key_tf,
                            ft.Text(
                                "Ключи сохраняются отдельно для каждого провайдера. "
                                "Для РФ без VPN подходят ProxyAPI и VseGPT.",
                                size=12,
                                color=ft.Colors.GREY_700
                            ),
                            ft.Divider(),
                            level_dd,
                            topic_tf,
                            tts_mode_dd,
                            stt_mode_dd,
                            auto_tts_sw,
                            ft.Button(
                                content="Сохранить настройки",
                                icon=ft.Icons.SAVE,
                                bgcolor=ft.Colors.BLUE_600,
                                color=ft.Colors.WHITE,
                                width=float("inf"),
                                height=48,
                                on_click=save_settings_click
                            ),
                            save_status_text,
                            ft.Divider(),
                            ft.Text(
                                f"Версия приложения: {get_app_version()}",
                                size=12,
                                color=ft.Colors.GREY_500,
                                text_align=ft.TextAlign.CENTER
                            )
                        ],
                        spacing=14,
                        scroll=ft.ScrollMode.AUTO
                    ),
                    expand=True
                )
            ],
            expand=True,
            spacing=0,
            visible=False
        )

        def on_nav_change(e):
            idx = e.control.selected_index
            chat_view.visible = (idx == 0)
            dict_view.visible = (idx == 1)
            settings_view.visible = (idx == 2)
            if idx == 1:
                load_dictionary()
            page.update()

        page.navigation_bar = ft.NavigationBar(
            selected_index=0,
            on_change=on_nav_change,
            destinations=[
                ft.NavigationBarDestination(icon=ft.Icons.CHAT_BUBBLE_OUTLINE, selected_icon=ft.Icons.CHAT_BUBBLE, label="Чат"),
                ft.NavigationBarDestination(icon=ft.Icons.BOOK_OUTLINED, selected_icon=ft.Icons.BOOK, label="Словарь"),
                ft.NavigationBarDestination(icon=ft.Icons.SETTINGS_OUTLINED, selected_icon=ft.Icons.SETTINGS, label="Настройки"),
            ]
        )

        load_chat_history()
        load_dictionary()

        page.add(
            ft.Stack([chat_view, dict_view, settings_view], expand=True)
        )

    except Exception:
        page.add(
            ft.Container(
                padding=20,
                content=ft.Text(
                    f"Ошибка запуска:\n{traceback.format_exc()}",
                    color=ft.Colors.RED_700,
                    size=12,
                    selectable=True
                )
            )
        )


if __name__ == "__main__":
    ft.run(main)
