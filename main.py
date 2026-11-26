# === ЧАСТЬ 1 из 6 (строки 1–145) ===
import os
import re
import json
import time
import wave
import sqlite3
import tempfile
import asyncio
import traceback
import urllib.parse
import tomllib
import httpx
import flet as ft
import flet_audio as fta
import flet_audio_recorder as far

STORAGE_DIR = os.getenv("FLET_APP_STORAGE_DATA") or tempfile.gettempdir()
DB_PATH = os.path.join(STORAGE_DIR, "tutor_data.db")


def _first(seq, default=None):
    """Безопасное получение нулевого элемента списка/кортежа."""
    if not seq:
        return default
    return next(iter(seq), default)


def _build_url(host_parts: list[str], path_parts: list[str], query: str = "") -> str:
    """Сборка URL по частям."""
    host = ".".join(host_parts)
    path = "/" + "/".join(path_parts) if path_parts else ""
    return urllib.parse.urlunparse(("https", host, path, "", query, ""))


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
        "suggest_dict": "0",
        "tts_mode": "Встроенный в Android (Google)",
        "stt_mode": "Встроенный в Android (Google)"
    }
    for k, v in defaults.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    cursor.execute("SELECT key, value FROM settings")
    for k, v in cursor.fetchall():
        cleaned = _clean_tuple_str(str(v))
        if cleaned != str(v):
            cursor.execute("UPDATE settings SET value = ? WHERE key = ?", (cleaned, k))

    cursor.execute(
        "UPDATE settings SET value = 'Встроенный в Android (Google)' "
        "WHERE key = 'stt_mode' AND value IN ('Бесплатный (Google Speech)', 'Средствами телефона (Android)')"
    )
    cursor.execute(
        "UPDATE settings SET value = 'Встроенный в Android (Google)' "
        "WHERE key = 'tts_mode' AND value IN ('Бесплатный голос (Google TTS)', 'Голос телефона (Android TTS)')"
    )

    cursor.execute("SELECT value FROM settings WHERE key = 'api_key'")
    old_val = _first(cursor.fetchone())
    if old_val:
        cursor.execute("SELECT value FROM settings WHERE key = 'provider'")
        prov_val = _first(cursor.fetchone(), "")
        target_key = get_provider_key_name(prov_val)
        cursor.execute("UPDATE settings SET value = ? WHERE key = ? AND value = ''", (old_val, target_key))
        cursor.execute("DELETE FROM settings WHERE key = 'api_key'")

    conn.commit()
    conn.close()


def get_setting(key: str) -> str:
    if key == "api_key":
        key = get_provider_key_name(get_setting("provider"))
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    val = _first(cursor.fetchone())
    conn.close()
    return _clean_tuple_str(str(val)) if val is not None else ""


def set_setting(key: str, value: str):
    if key == "api_key":
        key = get_provider_key_name(get_setting("provider"))
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()
# === ЧАСТЬ 2 из 6 (строки 146–285) ===
def get_ai_config() -> tuple[str, str, str]:
    provider = (get_setting("provider") or "ProxyAPI").strip()
    api_key = get_setting(get_provider_key_name(provider)).strip()

    if provider == "ProxyAPI":
        url = _build_url(["api", "proxyapi", "ru"], ["openai", "v1", "chat", "completions"])
        return url, api_key, "gpt-4o-mini"
    elif provider == "VseGPT":
        url = _build_url(["api", "vsegpt", "ru"], ["v1", "chat", "completions"])
        return url, api_key, "openai/gpt-4o-mini"
    else:
        url = _build_url(["openrouter", "ai"], ["api", "v1", "chat", "completions"])
        return url, api_key, "openrouter/free"


def get_system_prompt() -> str:
    level = get_setting("level") or "A1-A2 (Начальный)"
    topic = get_setting("topic") or "Путешествия и кафе"
    return f"""Ты — дружелюбный, терпеливый и профессиональный репетитор английского языка для русскоговорящего ученика.
Уровень ученика: {level}.
Текущая тема беседы: {topic}.

Правила общения:
1. Веди диалог преимущественно на английском языке, адаптируя сложность лексики и грамматики под уровень {level}.
2. После каждой своей реплики на английском обязательно давай её точный перевод на русский язык в круглых скобках: (перевод).
3. Если ученик допустил грамматическую или лексическую ошибку, мягко поправь его на русском языке в начале сообщения, приведи правильный вариант и затем продолжи диалог по теме.
4. Задавай в конце ответа один короткий вовлекающий вопрос на английском (с переводом в скобках), чтобы поддерживать беседу.
5. Отвечай лаконично (2-4 предложения на английском + перевод), без длинных лекций."""


def extract_english_part(text: str) -> str:
    """Извлекает исключительно английский текст для озвучки, удаляя русский язык и переводы."""
    # 1. Удаляем все фрагменты в круглых скобках (перевод на русский)
    cleaned = re.sub(r"\([^)]*\)", " ", text)
    # 2. Удаляем любые слова на кириллице (исправления ошибок на русском языке)
    cleaned = re.sub(r"[а-яА-ЯёЁ]+", " ", cleaned)
    # 3. Убираем символы Markdown (*, _, #, `, ~, <, >)
    cleaned = re.sub(r"[*_#`~<>]", " ", cleaned)
    # 4. Убираем пробелы перед знаками препинания и висячие дефисы
    cleaned = re.sub(r"\s+([.,!?;:])", r"\1", cleaned)
    cleaned = re.sub(r"\s-\s", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

class AndroidNativeSpeech:
    """Мост к встроенному в Android приложению 'Распознавание и синтез речи от Google'."""

    def __init__(self):
        self._tts = None
        self._tts_ready = False
        self._jnius_ok = False
        self._recognizer = None
        self._stt_text = ""
        self._stt_error = ""
        self._stt_done = False
        try:
            import jnius  # noqa: F401
            self._jnius_ok = True
        except Exception:
            self._jnius_ok = False

    def is_available(self) -> bool:
        return self._jnius_ok

    def _get_android_context(self):
        from jnius import autoclass
        try:
            ActivityThread = autoclass("android.app.ActivityThread")
            app = ActivityThread.currentApplication()
            if app is not None:
                return app.getApplicationContext()
        except Exception:
            pass
        try:
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            if PythonActivity.mActivity is not None:
                return PythonActivity.mActivity
        except Exception:
            pass
        return None

    def _ensure_tts(self) -> bool:
        if not self._jnius_ok:
            return False
        if self._tts is not None and self._tts_ready:
            return True
        try:
            from jnius import autoclass
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            Locale = autoclass("java.util.Locale")
            ctx = self._get_android_context()
            if ctx is None:
                return False

            try:
                self._tts = TextToSpeech(ctx, None, "com.google.android.tts")
            except Exception:
                self._tts = TextToSpeech(ctx, None)

            time.sleep(0.35)
            self._tts.setLanguage(Locale.US)
            self._tts_ready = True
            return True
        except Exception:
            self._tts_ready = False
            return False

    def speak_english(self, text: str) -> bool:
        """Озвучивает английский текст встроенным синтезатором речи Android."""
        if not text.strip():
            return False
        if not self._ensure_tts():
            return False
        try:
            from jnius import autoclass
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            Locale = autoclass("java.util.Locale")
            self._tts.setLanguage(Locale.US)
            self._tts.speak(text, TextToSpeech.QUEUE_FLUSH, None, "tutor_en_tts")
            return True
        except Exception:
            return False

    def recognize_system_speech_blocking(self, language: str = "en-US", max_sec: float = 8.0) -> tuple[str, str]:
        """
        Пытается распознать речь через системный android.speech.SpeechRecognizer
        без использования PythonJavaClass (безопасно для Serious Python).
        """
        if not self._jnius_ok:
            return "", "jnius недоступен"
        try:
            from jnius import autoclass
            SpeechRecognizer = autoclass("android.speech.SpeechRecognizer")
            ctx = self._get_android_context()
            if ctx is None or not SpeechRecognizer.isRecognitionAvailable(ctx):
                return "", "Служба Google Speech недоступна на устройстве"
            # В среде Serious Python/Flet без Java-обёртки RecognitionListener
            # прямой фоновый SpeechRecognizer не может передать Bundle обратно в Python.
            return "", "Требуется системный голосовой ввод клавиатуры или Whisper API"
        except Exception as e:
            return "", str(e)

# === ЧАСТЬ 3 из 6 (строки 286–430) ===
class TutorApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.page.title = "AI English Tutor"
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.padding = 0
        self.page.bgcolor = "#F4F6F9"

        self.is_recording = False
        self.record_path = os.path.join(STORAGE_DIR, "voice_input.wav")
        self.tts_path = os.path.join(STORAGE_DIR, "tts_output.mp3")
        self.is_loading = False
        self.android_speech = AndroidNativeSpeech()

        self.audio_player = fta.Audio()
        self.audio_recorder = far.AudioRecorder()
        self.page.services.extend([self.audio_player, self.audio_recorder])

        self.init_ui()
        self.load_history()

    def show_snack(self, text: str, is_error: bool = False, duration_ms: int = 3000):
        snack = ft.SnackBar(
            content=ft.Text(text, color=ft.Colors.WHITE),
            bgcolor=ft.Colors.RED_600 if is_error else ft.Colors.INDIGO_600,
            duration=duration_ms
        )
        self.page.show_dialog(snack)

    async def speak_text(self, full_text: str):
        # Озвучиваем исключительно английский текст (без русских слов и переводов)
        en_text = extract_english_part(full_text)
        if not en_text:
            return
        tts_mode = get_setting("tts_mode") or "Встроенный в Android (Google)"

        # 1. Встроенный в Android синтезатор ("Распознавание и синтез речи от Google")
        if tts_mode == "Встроенный в Android (Google)":
            ok = await asyncio.to_thread(self.android_speech.speak_english, en_text)
            if ok:
                return

        # 2. Облачный OpenAI TTS (через ProxyAPI / VseGPT)
        provider = (get_setting("provider") or "ProxyAPI").strip()
        api_key = get_setting(get_provider_key_name(provider)).strip()
        if tts_mode == "OpenAI TTS (через API)" and api_key and provider in ("ProxyAPI", "VseGPT"):
            try:
                if provider == "ProxyAPI":
                    tts_url = _build_url(["api", "proxyapi", "ru"], ["openai", "v1", "audio", "speech"])
                else:
                    tts_url = _build_url(["api", "vsegpt", "ru"], ["v1", "audio", "speech"])
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                payload = {"model": "tts-1", "input": en_text, "voice": "nova"}
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(tts_url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        with open(self.tts_path, "wb") as f:
                            f.write(resp.content)
                        self.audio_player.src = self.tts_path
                        self.audio_player.update()
                        await asyncio.sleep(0.1)
                        await self.audio_player.play()
                        return
            except Exception:
                pass

        # 3. Запасной вариант синтеза речи (Google Translate TTS на английском)
        try:
            q = urllib.parse.quote(en_text[:200])
            url = _build_url(
                ["translate", "googleapis", "com"],
                ["translate_tts"],
                f"ie=UTF-8&q={q}&tl=en&client=tw-ob"
            )
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    with open(self.tts_path, "wb") as f:
                        f.write(resp.content)
                    self.audio_player.src = self.tts_path
                    self.audio_player.update()
                    await asyncio.sleep(0.1)
                    await self.audio_player.play()
        except Exception as e:
            self.show_snack(f"Не удалось озвучить: {e}", is_error=True)

    async def on_mic_click(self, e):
        stt_mode = get_setting("stt_mode") or "Встроенный в Android (Google)"

        # Если выбран встроенный в Android способ — используем встроенный голосовой ввод системы,
        # НЕ переключаясь на ИИ (Whisper)!
        if stt_mode == "Встроенный в Android (Google)":
            await self.msg_input.focus()
            self.show_snack(
                "Нажмите микрофон на клавиатуре (Google) — обработает встроенный движок Android",
                duration_ms=2000
            )
            return

        # Режим Whisper API (ИИ): запись WAV -> отправка в Whisper
        if not self.is_recording:
            try:
                has_perm = await self.audio_recorder.has_permission()
                if not has_perm:
                    self.show_snack("Нет разрешения на использование микрофона!", is_error=True)
                    return
                if os.path.exists(self.record_path):
                    os.remove(self.record_path)
                await self.audio_recorder.start_recording(
                    output_path=self.record_path,
                    configuration=far.AudioRecorderConfiguration(
                        encoder=far.AudioEncoder.WAV,
                        sample_rate=16000
                    )
                )

                self.is_recording = True
                self.mic_btn.icon = ft.Icons.STOP_CIRCLE
                self.mic_btn.icon_color = ft.Colors.RED_600
                self.mic_btn.tooltip = "Нажмите для обработки голоса"
                self.mic_btn.update()
                self.show_snack("Говорите...", duration_ms=500)
            except Exception as ex:
                self.is_recording = False
                self.show_snack(f"Ошибка микрофона: {ex}", is_error=True)
        else:
            try:
                await self.audio_recorder.stop_recording()
            except Exception:
                pass
            self.is_recording = False
            self.mic_btn.icon = ft.Icons.MIC
            self.mic_btn.icon_color = ft.Colors.INDIGO_600
            self.mic_btn.tooltip = "Голосовой ввод"
            self.mic_btn.update()
            await asyncio.sleep(0.15)
            await self.process_voice_file()

    async def process_voice_file(self):
        if not os.path.exists(self.record_path) or os.path.getsize(self.record_path) < 800:
            self.show_snack("Запись слишком короткая или пустая.", is_error=True)
            return

        stt_mode = get_setting("stt_mode") or "Встроенный в Android (Google)"
        if stt_mode == "Встроенный в Android (Google)":
            # Строго не используем ИИ, если выбран встроенный режим
            self.show_snack("Обработано: Встроенный Android (Google)", duration_ms=500)
            return

        provider = (get_setting("provider") or "ProxyAPI").strip()
        api_key = get_setting(get_provider_key_name(provider)).strip()

        if not api_key or provider not in ("ProxyAPI", "VseGPT"):
            self.show_snack("Для Whisper API укажите ключ ProxyAPI или VseGPT в Настройках.", is_error=True)
            return

        try:
            if provider == "ProxyAPI":
                stt_url = _build_url(["api", "proxyapi", "ru"], ["openai", "v1", "audio", "transcriptions"])
            else:
                stt_url = _build_url(["api", "vsegpt", "ru"], ["v1", "audio", "transcriptions"])

            headers = {"Authorization": f"Bearer {api_key}"}
            with open(self.record_path, "rb") as audio_file:
                files = {"file": ("voice.wav", audio_file, "audio/wav")}
                data = {"model": "whisper-1", "language": "en"}
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(stt_url, headers=headers, data=data, files=files)
                    if resp.status_code == 200:
                        text = resp.json().get("text", "").strip()
                        if text:
                            self.msg_input.value = text
                            self.msg_input.update()
                            self.show_snack("Обработано: ИИ (Whisper)", duration_ms=500)
                        else:
                            self.show_snack("Не удалось разобрать слова.", is_error=True)
                    else:
                        self.show_snack(f"Ошибка Whisper ({resp.status_code}): {resp.text[:100]}", is_error=True)
        except Exception as e:
            self.show_snack(f"Ошибка распознавания: {e}", is_error=True)

    async def ask_ai(self, user_text: str):
        url, api_key, model = get_ai_config()
        provider = (get_setting("provider") or "ProxyAPI").strip()
        if not api_key:
            self.show_snack(f"Сначала укажите API-ключ для {provider} в Настройках!", is_error=True)
            return

        self.is_loading = True
        self.loading_bar.visible = True
        self.send_btn.disabled = True
        self.page.update()

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO messages (role, content) VALUES (?, ?)", ("user", user_text))
        conn.commit()
        cursor.execute("SELECT role, content FROM messages ORDER BY id DESC LIMIT 12")
        rows = cursor.fetchall()
        conn.close()

        history = [{"role": "system", "content": get_system_prompt()}]
        for r, c in reversed(rows):
            history.append({"role": r, "content": c})

        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model,
                "messages": history,
                "temperature": 0.7,
                "max_tokens": 500
            }

            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code != 200 and provider == "ProxyAPI":
                    alt_url = _build_url(["api", "proxyapi", "ru"], ["v1", "chat", "completions"])
                    alt_payload = dict(payload, model="openai/gpt-4o-mini")
                    resp = await client.post(alt_url, headers=headers, json=alt_payload)

                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
                data = resp.json()
                first_choice = _first(data.get("choices"), {})
                ai_reply = (first_choice.get("message", {}).get("content") or "").strip()
                if not ai_reply:
                    raise RuntimeError("Пустой ответ от модели ИИ.")

            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO messages (role, content) VALUES (?, ?)", ("assistant", ai_reply))
            conn.commit()
            conn.close()

            self.add_message_bubble("assistant", ai_reply)

            if get_setting("auto_tts") == "1":
                await self.speak_text(ai_reply)
            if get_setting("suggest_dict") == "1" and self._extract_important_english_words(ai_reply):
                self.save_phrase_dialog(ai_reply)


        except Exception as ex:
            self.show_snack(f"Ошибка ИИ: {ex}", is_error=True)
        finally:
            self.is_loading = False
            self.loading_bar.visible = False
            self.send_btn.disabled = False
            self.page.update()
# === ЧАСТЬ 5 из 6 (строки 596–765) ===
    def add_message_bubble(self, role: str, content: str):
        is_user = role == "user"
        controls = [
            ft.Text(
                content,
                color=ft.Colors.WHITE if is_user else ft.Colors.BLACK_87,
                size=15,
                selectable=True
            )
        ]

        if not is_user:
            action_buttons = [
                ft.IconButton(
                    icon=ft.Icons.VOLUME_UP,
                    icon_size=18,
                    icon_color=ft.Colors.INDIGO_600,
                    tooltip="Озвучить английский текст",
                    on_click=lambda e, txt=content: self.page.run_task(self.speak_text, txt)
                )
            ]
            if get_setting("suggest_dict") == "1":
                action_buttons.append(
                    ft.IconButton(
                        icon=ft.Icons.BOOKMARK_ADD_OUTLINED,
                        icon_size=18,
                        icon_color=ft.Colors.TEAL_600,
                        tooltip="Сохранить слово в словарь",
                        on_click=lambda e, txt=content: self.save_phrase_dialog(txt)
                    )
                )
            controls.append(ft.Row(action_buttons, spacing=0, tight=True))

        bubble = ft.Container(
            content=ft.Column(controls, spacing=4, tight=True),
            bgcolor=ft.Colors.INDIGO_600 if is_user else ft.Colors.WHITE,
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            border_radius=ft.BorderRadius.only(
                top_left=16,
                top_right=16,
                bottom_left=4 if is_user else 16,
                bottom_right=16 if is_user else 4
            ),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=4,
                color=ft.Colors.with_opacity(0.08, ft.Colors.BLACK),
                offset=ft.Offset(0, 2)
            ),
            width=310
        )

        row = ft.Row(
            [bubble],
            alignment=ft.MainAxisAlignment.END if is_user else ft.MainAxisAlignment.START
        )
        self.chat_list.controls.append(row)
        self.chat_list.update()

    def _extract_important_english_words(self, full_text: str) -> list[str]:
        """Извлекает уникальные важные английские слова из текста сообщения."""
        en_only = extract_english_part(full_text)
        raw_words = re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)*", en_only)
        # фильтруются артикли, местоимения, предлоги, союзы и вспомогательные глаголы
        stop_words = {
            "a", "an", "the", "i", "you", "he", "she", "it", "we", "they",
            "me", "him", "her", "us", "them", "my", "your", "his", "its", "our", "their",
            "am", "is", "are", "was", "were", "be", "been", "being",
            "do", "does", "did", "have", "has", "had",
            "will", "would", "shall", "should", "can", "could", "may", "might", "must",
            "to", "of", "in", "on", "at", "by", "for", "with", "about", "as", "into",
            "and", "or", "but", "if", "so", "than", "that", "this", "these", "those",
            "what", "which", "who", "whom", "whose", "where", "when", "why", "how",
            "not", "no", "yes", "ok", "okay", "hi", "hello", "hey", "please", "thanks", "thank"
        }

        result = []
        seen = set()
        for w in raw_words:
            low = w.lower()
            if len(low) < 3 or low in stop_words or low in seen:
                continue
            seen.add(low)
            result.append(low)
        return result

    async def _translate_word_to_ru(self, word: str) -> str:
        """Получает перевод одного английского слова на русский язык."""
        url, api_key, model = get_ai_config()
        if api_key:
            try:
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Переведи одно английское слово на русский язык. Верни только 1–2 основных значения через запятую, без пояснений и знаков препинания в конце."
                        },
                        {"role": "user", "content": word}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 30
                }
                async with httpx.AsyncClient(timeout=12.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        tr = (_first(data.get("choices"), {}).get("message", {}).get("content") or "").strip()
                        if tr:
                            return tr
            except Exception:
                pass

        # Запасной бесплатный переводчик, если ИИ недоступен
        try:
            q = urllib.parse.quote(word)
            t_url = _build_url(
                ["translate", "googleapis", "com"],
                ["translate_a", "single"],
                f"client=gtx&sl=en&tl=ru&dt=t&q={q}"
            )
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(t_url)
                if resp.status_code == 200:
                    data = resp.json()
                    tr = data[0][0][0].strip()
                    if tr:
                        return tr
        except Exception:
            pass

        return "—"

    async def _save_single_word_task(self, word: str):
        """Сохраняет выбранное слово в словарь вместе с переводом."""
        translation = await self._translate_word_to_ru(word)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO dictionary (word, translation) VALUES (?, ?)",
            (word, translation)
        )
        conn.commit()
        conn.close()
        if self.dict_view.visible:
            self.refresh_dictionary()

    def save_phrase_dialog(self, full_text: str):
        words = self._extract_important_english_words(full_text)
        if not words:
            self.show_snack("Нет подходящих английских слов", duration_ms=1000)
            return

        def on_word_click(e, selected_word: str):
            self.page.pop_dialog()
            self.show_snack("слово сохранено", duration_ms=300)
            self.page.run_task(self._save_single_word_task, selected_word)

        word_buttons = [
            ft.FilledTonalButton(
                content=ft.Text(w, size=14),
                on_click=lambda e, w=w: on_word_click(e, w)
            )
            for w in words
        ]

        dlg = ft.AlertDialog(
            title=ft.Text("Выберите слово для словаря"),
            content=ft.Container(
                content=ft.Row(word_buttons, wrap=True, spacing=8, run_spacing=8),
                width=300
            ),
            actions=[
                ft.TextButton(content=ft.Text("Отмена"), on_click=lambda e: self.page.pop_dialog())
            ]
        )
        self.page.show_dialog(dlg)

    def load_history(self):
        self.chat_list.controls.clear()
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT role, content FROM messages ORDER BY id ASC")
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            welcome = (
                "Hello! I am your AI English tutor. Let's practice together! "
                "(Привет! Я твой ИИ-репетитор английского. Давай практиковаться вместе!) "
                "How are you today? (Как твои дела сегодня?)"
            )
            self.add_message_bubble("assistant", welcome)
        else:
            for r, c in rows:
                self.add_message_bubble(r, c)

    def refresh_dictionary(self):
        self.dict_list.controls.clear()
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, word, translation FROM dictionary ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            self.dict_list.controls.append(
                ft.Container(
                    content=ft.Text("Словарь пока пуст. Сохраняйте фразы из чата!", color=ft.Colors.GREY_600),
                    padding=20,
                    alignment=ft.Alignment.CENTER
                )
            )
        else:
            for item_id, w, t in rows:
                self.dict_list.controls.append(
                    ft.Card(
                        content=ft.ListTile(
                            title=ft.Text(w, weight=ft.FontWeight.BOLD),
                            subtitle=ft.Text(t),
                            trailing=ft.Row(
                                [
                                    ft.IconButton(
                                        icon=ft.Icons.VOLUME_UP,
                                        icon_color=ft.Colors.INDIGO_600,
                                        on_click=lambda e, txt=w: self.page.run_task(self.speak_text, txt)
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.DELETE_OUTLINE,
                                        icon_color=ft.Colors.RED_400,
                                        on_click=lambda e, i=item_id: self.delete_dict_item(i)
                                    )
                                ],
                                tight=True
                            )
                        )
                    )
                )
        self.dict_list.update()

    def delete_dict_item(self, item_id: int):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM dictionary WHERE id = ?", (item_id,))
        conn.commit()
        conn.close()
        self.refresh_dictionary()

    def show_about_dialog(self, e):
        app_ver = get_app_version()
        about_dlg = ft.AlertDialog(
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.SCHOOL, color=ft.Colors.INDIGO_600),
                    ft.Text("О приложении", weight=ft.FontWeight.BOLD)
                ],
                spacing=8
            ),
            content=ft.Column(
                [
                    ft.Text("AI English Tutor", size=17, weight=ft.FontWeight.BOLD),
                    ft.Text(f"Версия: {app_ver}", size=13, color=ft.Colors.GREY_700),
                    ft.Divider(height=12),
                    ft.Text(
                        "Персональный ИИ-репетитор английского языка для практики живого общения, "
                        "разбора ошибок и пополнения словарного запаса.",
                        size=14
                    ),
                    ft.Text(
                        "• Диалоги с ИИ под ваш уровень (A1–C2)\n"
                        "• Озвучка только английской речи\n"
                        "• Встроенный голосовой ввод и синтез Android (Google)\n"
                        "• Личный словарь с озвучкой",
                        size=13,
                        color=ft.Colors.BLACK_87
                    )
                ],
                tight=True,
                spacing=8,
                width=310
            ),
            actions=[
                ft.TextButton(content=ft.Text("Закрыть"), on_click=lambda ev: self.page.pop_dialog())
            ],
            actions_alignment=ft.MainAxisAlignment.END
        )
        self.page.show_dialog(about_dlg)
# === ЧАСТЬ 6 из 6 (строки 766–925) ===
    def init_ui(self):
        # --- Вкладка 1: Чат ---
        self.chat_list = ft.ListView(expand=True, spacing=10, padding=12, auto_scroll=True)
        self.loading_bar = ft.ProgressBar(visible=False, color=ft.Colors.INDIGO_600)

        self.msg_input = ft.TextField(
            hint_text="Напишите или скажите по-английски...",
            expand=True,
            border_radius=22,
            filled=True,
            bgcolor=ft.Colors.WHITE,
            content_padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            on_submit=lambda e: self.page.run_task(self.on_send_click, e)
        )
        self.mic_btn = ft.IconButton(
            icon=ft.Icons.MIC,
            icon_color=ft.Colors.INDIGO_600,
            tooltip="Голосовой ввод",
            on_click=lambda e: self.page.run_task(self.on_mic_click, e)
        )
        self.send_btn = ft.IconButton(
            icon=ft.Icons.SEND_ROUNDED,
            icon_color=ft.Colors.INDIGO_600,
            tooltip="Отправить",
            on_click=lambda e: self.page.run_task(self.on_send_click, e)
        )

        self.chat_view = ft.Column(
            [
                self.chat_list,
                self.loading_bar,
                ft.Container(
                    content=ft.Row([self.mic_btn, self.msg_input, self.send_btn], spacing=4),
                    padding=ft.Padding.symmetric(horizontal=8, vertical=6),
                    bgcolor="#EEF1F6"
                )
            ],
            expand=True,
            spacing=0
        )

        # --- Вкладка 2: Словарь ---
        self.dict_list = ft.ListView(expand=True, spacing=8, padding=12)
        self.dict_view = ft.Column([self.dict_list], expand=True, visible=False)

        # --- Вкладка 3: Настройки ---
        self.provider_dd = ft.Dropdown(
            label="Провайдер ИИ",
            value=get_setting("provider") or "ProxyAPI",
            options=[
                ft.dropdown.Option("ProxyAPI"),
                ft.dropdown.Option("VseGPT"),
                ft.dropdown.Option("OpenRouter")
            ],
            on_select=self.on_provider_change
        )
        cur_prov = self.provider_dd.value
        self.api_key_field = ft.TextField(
            label=f"API-ключ ({cur_prov})",
            value=get_setting(get_provider_key_name(cur_prov)),
            password=True,
            can_reveal_password=True
        )
        self.level_dd = ft.Dropdown(
            label="Уровень английского",
            value=get_setting("level") or "A1-A2 (Начальный)",
            options=[
                ft.dropdown.Option("A1-A2 (Начальный)"),
                ft.dropdown.Option("B1-B2 (Средний)"),
                ft.dropdown.Option("C1-C2 (Продвинутый)")
            ]
        )
        self.topic_field = ft.TextField(
            label="Тема беседы",
            value=get_setting("topic") or "Путешествия и кафе"
        )
        self.stt_dd = ft.Dropdown(
            label="Распознавание речи (микрофон)",
            value=get_setting("stt_mode") or "Встроенный в Android (Google)",
            options=[
                ft.dropdown.Option("Встроенный в Android (Google)"),
                ft.dropdown.Option("Whisper API (через ProxyAPI / VseGPT)")
            ]
        )
        self.tts_dd = ft.Dropdown(
            label="Озвучивание речи (только EN)",
            value=get_setting("tts_mode") or "Встроенный в Android (Google)",
            options=[
                ft.dropdown.Option("Встроенный в Android (Google)"),
                ft.dropdown.Option("OpenAI TTS (через API)")
            ]
        )
        self.auto_tts_sw = ft.Switch(
            label="Автоматически озвучивать ответы учителя",
            value=get_setting("auto_tts") == "1"
        )
        self.suggest_dict_sw = ft.Switch(
            label="Предлагать сохранение в словарь",
            value=get_setting("suggest_dict") == "1"
        )

        save_settings_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.SAVE, color=ft.Colors.WHITE), ft.Text("Сохранить настройки", color=ft.Colors.WHITE)],
                tight=True
            ),
            bgcolor=ft.Colors.INDIGO_600,
            on_click=self.on_save_settings
        )
        clear_chat_btn = ft.OutlinedButton(
            content=ft.Row(
                [ft.Icon(ft.Icons.DELETE_SWEEP), ft.Text("Очистить историю чата")],
                tight=True
            ),
            on_click=self.on_clear_chat
        )
        about_btn = ft.OutlinedButton(
            content=ft.Row(
                [ft.Icon(ft.Icons.INFO_OUTLINE), ft.Text("О приложении")],
                tight=True
            ),
            on_click=self.show_about_dialog
        )

        self.settings_view = ft.ListView(
            [
                ft.Text("Настройки репетитора", size=18, weight=ft.FontWeight.BOLD),
                self.provider_dd,
                self.api_key_field,
                self.level_dd,
                self.topic_field,
                ft.Divider(),
                ft.Text("Голос и речь", size=16, weight=ft.FontWeight.BOLD),
                self.stt_dd,
                self.tts_dd,
                self.auto_tts_sw,
                self.suggest_dict_sw,
                ft.Divider(),
                ft.Row([save_settings_btn, clear_chat_btn], wrap=True, spacing=10),
                ft.Container(height=6),
                about_btn
            ],
            expand=True,
            spacing=12,
            padding=16,
            visible=False
        )

        self.nav_bar = ft.NavigationBar(
            selected_index=0,
            on_change=self.on_nav_change,
            destinations=[
                ft.NavigationBarDestination(icon=ft.Icons.CHAT_BUBBLE_OUTLINE, label="Чат"),
                ft.NavigationBarDestination(icon=ft.Icons.BOOK_OUTLINED, label="Словарь"),
                ft.NavigationBarDestination(icon=ft.Icons.SETTINGS_OUTLINED, label="Настройки"),
            ]
        )

        self.page.appbar = ft.AppBar(
            title=ft.Text("AI English Tutor", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
            bgcolor=ft.Colors.INDIGO_600,
            center_title=True
        )
        self.page.navigation_bar = self.nav_bar
        self.page.add(
            ft.Container(
                content=ft.Stack([self.chat_view, self.dict_view, self.settings_view], expand=True),
                expand=True
            )
        )

    async def on_send_click(self, e):
        if self.is_loading:
            return
        text = (self.msg_input.value or "").strip()
        if not text:
            return
        self.msg_input.value = ""
        self.msg_input.update()
        self.add_message_bubble("user", text)
        await self.ask_ai(text)

    def on_provider_change(self, e):
        prov = self.provider_dd.value
        self.api_key_field.label = f"API-ключ ({prov})"
        self.api_key_field.value = get_setting(get_provider_key_name(prov))
        self.api_key_field.update()

    def on_save_settings(self, e):
        prov = self.provider_dd.value
        set_setting("provider", prov)
        set_setting(get_provider_key_name(prov), (self.api_key_field.value or "").strip())
        set_setting("level", self.level_dd.value)
        set_setting("topic", (self.topic_field.value or "").strip() or "Путешествия и кафе")
        set_setting("stt_mode", self.stt_dd.value)
        set_setting("tts_mode", self.tts_dd.value)
        set_setting("auto_tts", "1" if self.auto_tts_sw.value else "0")
        set_setting("suggest_dict", "1" if self.suggest_dict_sw.value else "0")
        self.load_history()
        self.show_snack("Сохранено", duration_ms=500)

    def on_clear_chat(self, e):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages")
        conn.commit()
        conn.close()
        self.load_history()
        self.show_snack("Выполнено", duration_ms=1000)

    def on_nav_change(self, e):
        idx = self.nav_bar.selected_index
        self.chat_view.visible = (idx == 0)
        self.dict_view.visible = (idx == 1)
        self.settings_view.visible = (idx == 2)
        if idx == 1:
            self.refresh_dictionary()
        self.page.update()


def main(page: ft.Page):
    init_db()
    TutorApp(page)


if __name__ == "__main__":
    ft.run(main)
