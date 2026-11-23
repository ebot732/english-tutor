# === ФРАГМЕНТ 1 из 7 ===
import os
import re
import json
import time
import base64
import sqlite3
import tempfile
import asyncio
import threading
import traceback
import urllib.request
import urllib.parse
import urllib.error
import tomllib
import httpx
import flet as ft
import flet_audio as fta
import flet_audio_recorder as far

try:
    from jnius import autoclass, PythonJavaClass, java_method
except ImportError:
    autoclass = None
    PythonJavaClass = object
    def java_method(*args, **kwargs):
        return lambda f: f

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
        "tts_mode": "Бесплатный голос (Android TTS)",
        "stt_mode": "Средствами телефона (Android)"
    }
    for k, v in defaults.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # Автоочистка значений, сохраненных ранее со скобками кортежа ('...',)
    cursor.execute("SELECT key, value FROM settings")
    for k, v in cursor.fetchall():
        cleaned = _clean_tuple_str(str(v))
        if cleaned != str(v):
            cursor.execute("UPDATE settings SET value = ? WHERE key = ?", (cleaned, k))

    cursor.execute("SELECT value FROM settings WHERE key = 'api_key'")
    old_row = cursor.fetchone()
    if old_row and old_row[0]:
        cursor.execute("SELECT value FROM settings WHERE key = 'provider'")
        prov_row = cursor.fetchone()
        target_key = get_provider_key_name(prov_row[0] if prov_row else "")
        cursor.execute("UPDATE settings SET value = ? WHERE key = ? AND value = ''", (old_row[0], target_key))
        cursor.execute("DELETE FROM settings WHERE key = 'api_key'")

    conn.commit()
    conn.close()

# === ФРАГМЕНТ 2 из 7 ===
def get_setting(key: str) -> str:
    if key == "api_key":
        key = get_provider_key_name(get_setting("provider"))
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return _clean_tuple_str(str(row[0])) if row and row[0] is not None else ""


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
        return "https://api.proxyapi.ru/v1", api_key, "openai/gpt-4o-mini"
    elif provider == "VseGPT":
        return "https://api.vsegpt.ru/v1", api_key, "openai/gpt-4o-mini"
    else:
        return "https://openrouter.ai/api/v1", api_key, "openrouter/free"


async def ask_llm(messages: list[dict], temperature: float = 0.7) -> str:
    base_url, api_key, model = get_ai_config()
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com",
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
            return data["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as e:
            err_body = e.response.text
            raise RuntimeError(f"Ошибка сервера {e.response.status_code} ({base_url}): {err_body[:200]}")
        except httpx.RequestError as e:
            raise RuntimeError(f"Ошибка сети ({base_url}): {e}")


def _get_android_activity():
    if autoclass is None:
        return None
    for cls_name in ("com.flet.app.MainActivity", "org.kivy.android.PythonActivity"):
        try:
            cls = autoclass(cls_name)
            if hasattr(cls, "mActivity") and cls.mActivity is not None:
                return cls.mActivity
        except Exception:
            pass
    try:
        ActivityThread = autoclass("android.app.ActivityThread")
        app = ActivityThread.currentApplication()
        if app is not None:
            return app
    except Exception:
        pass
    return None

# === ФРАГМЕНТ 3 из 7 ===
def _sync_android_stt() -> str:
    if autoclass is None:
        raise RuntimeError("Распознавание средствами телефона доступно только в APK на Android.")
    try:
        Intent = autoclass("android.content.Intent")
        RecognizerIntent = autoclass("android.speech.RecognizerIntent")
        SpeechRecognizer = autoclass("android.speech.SpeechRecognizer")
        Looper = autoclass("android.os.Looper")
        Handler = autoclass("android.os.Handler")

        context = _get_android_activity()
        if context is None:
            raise RuntimeError("Не удалось получить контекст Android.")

        if not SpeechRecognizer.isRecognitionAvailable(context):
            raise RuntimeError("На телефоне не установлен сервис распознавания речи Google (приложение Google).")

        result_holder = {"text": "", "error": None}
        done_event = threading.Event()

        class RecognitionListener(PythonJavaClass):
            __javainterfaces__ = ["android/speech/RecognitionListener"]
            __javacontext__ = "app"

            @java_method("(Landroid/os/Bundle;)V")
            def onReadyForSpeech(self, params):
                pass

            @java_method("()V")
            def onBeginningOfSpeech(self):
                pass

            @java_method("(F)V")
            def onRmsChanged(self, rmsdB):
                pass

            @java_method("([B)V")
            def onBufferReceived(self, buffer):
                pass

            @java_method("()V")
            def onEndOfSpeech(self):
                pass

            @java_method("(I)V")
            def onError(self, error):
                err_map = {
                    1: "Сетевая ошибка",
                    2: "Ошибка сети",
                    3: "Ошибка записи аудио",
                    4: "Ошибка сервера Google",
                    5: "Ошибка клиента",
                    6: "Речь не услышана (таймаут)",
                    7: "Речь не распознана",
                    8: "Распознаватель занят",
                    9: "Нет разрешения на микрофон"
                }
                result_holder["error"] = err_map.get(int(error), f"Код ошибки распознавания: {error}")
                done_event.set()

            @java_method("(Landroid/os/Bundle;)V")
            def onResults(self, results):
                try:
                    matches = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    if matches and matches.size() > 0:
                        result_holder["text"] = str(matches.get(0))
                except Exception as ex:
                    result_holder["error"] = str(ex)
                finally:
                    done_event.set()

            @java_method("(Landroid/os/Bundle;)V")
            def onPartialResults(self, partialResults):
                pass

            @java_method("(ILandroid/os/Bundle;)V")
            def onEvent(self, eventType, params):
                pass

        class StartRunnable(PythonJavaClass):
            __javainterfaces__ = ["java/lang/Runnable"]
            __javacontext__ = "app"

            @java_method("()V")
            def run(self):
                try:
                    sr = SpeechRecognizer.createSpeechRecognizer(context)
                    listener = RecognitionListener()
                    sr.setRecognitionListener(listener)
                    intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                    intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
                    intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                    sr.startListening(intent)
                except Exception as ex:
                    result_holder["error"] = str(ex)
                    done_event.set()

        main_handler = Handler(Looper.getMainLooper())
        main_handler.post(StartRunnable())
        done_event.wait(timeout=15.0)

        if result_holder["error"]:
            raise RuntimeError(result_holder["error"])
        return result_holder["text"].strip()
    except Exception as e:
        raise RuntimeError(f"Ошибка распознавания телефона: {e}")


async def recognize_audio_async(file_path: str) -> str:
    stt_mode = get_setting("stt_mode").strip() or "Средствами телефона (Android)"

    if stt_mode == "Средствами телефона (Android)":
        return await asyncio.to_thread(_sync_android_stt)

    if not os.path.exists(file_path) or os.path.getsize(file_path) < 100:
        raise RuntimeError("Файл записи пуст или не создан")

    provider = get_setting("provider").strip()
    base_url, api_key, _ = get_ai_config()

    if provider in ("ProxyAPI", "VseGPT") and api_key:
        whisper_base = "https://proxyapi.ru" if provider == "ProxyAPI" else base_url
        url = f"{whisper_base}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {api_key}"}
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            with open(file_path, "rb") as audio_file:
                files = {"file": (os.path.basename(file_path), audio_file, "audio/mp4")}
                data = {"model": "whisper-1", "language": "en"}
                response = await client.post(url, headers=headers, data=data, files=files)
                if response.status_code == 401:
                    raise RuntimeError("Неверный API-ключ для Whisper. Проверьте ключ в Настройках или переключитесь на распознавание телефоном.")
                response.raise_for_status()
                res_json = response.json()
                return (res_json.get("text") or "").strip()

    raise RuntimeError("Для распознавания через ИИ нужен ключ ProxyAPI или VseGPT. Переключите режим микрофона на «Средствами телефона (Android)» в Настройках.")

# === ФРАГМЕНТ 4 из 7 ===
async def get_tts_base64_async(clean_text: str) -> str:
    normalized = " ".join(clean_text.split())[:400]
    base_url, api_key, _ = get_ai_config()
    provider = get_setting("provider").strip()
    last_err = "Нет доступного сервиса озвучки"

    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        if provider in ("ProxyAPI", "VseGPT") and api_key:
            try:
                tts_base = "https://proxyapi.ru" if provider == "ProxyAPI" else base_url
                url = f"{tts_base}/audio/speech"
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
                resp.raise_for_status()
                if len(resp.content) > 500:
                    return base64.b64encode(resp.content).decode("utf-8")
            except Exception as e:
                last_err = f"TTS API: {e}"

        free_urls = [
            f"https://streamelements.com?{urllib.parse.urlencode({'voice': 'Brian', 'text': normalized[:200]})}",
            f"https://google.com?{urllib.parse.urlencode({'ie': 'UTF-8', 'q': normalized[:200], 'tl': 'en', 'client': 'gtx'})}"
        ]
        for url in free_urls:
            try:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200 and len(resp.content) > 500 and not resp.content.lstrip().startswith(b"<"):
                    return base64.b64encode(resp.content).decode("utf-8")
            except Exception as e:
                last_err = str(e)

    raise RuntimeError(last_err)


_android_tts_instance = None
_android_tts_ready = threading.Event()


def _sync_android_tts(text: str) -> None:
    global _android_tts_instance
    if autoclass is None:
        raise RuntimeError("Озвучка телефоном доступна только в APK на Android.")
    try:
        TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
        Locale = autoclass("java.util.Locale")
        context = _get_android_activity()
        if context is None:
            raise RuntimeError("Не удалось получить контекст Android.")

        if _android_tts_instance is None:
            class TTSInitListener(PythonJavaClass):
                __javainterfaces__ = ["android/speech/tts/TextToSpeech$OnInitListener"]
                __javacontext__ = "app"

                @java_method("(I)V")
                def onInit(self, status):
                    _android_tts_ready.set()

            listener = TTSInitListener()
            _android_tts_instance = TextToSpeech(context, listener)
            _android_tts_ready.wait(timeout=3.0)

        _android_tts_instance.setLanguage(Locale.US)
        _android_tts_instance.setSpeechRate(0.9)
        _android_tts_instance.speak(text, 0, None, f"utt_{int(time.time() * 1000)}")
    except Exception as e:
        raise RuntimeError(f"Ошибка Android TTS: {e}")


def _play_mp3_android_native(file_path: str) -> bool:
    if autoclass is None:
        return False
    try:
        MediaPlayer = autoclass("android.media.MediaPlayer")
        mp = MediaPlayer()
        mp.setDataSource(file_path)
        mp.prepare()
        mp.start()
        return True
    except Exception:
        return False


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

# === ФРАГМЕНТ 5 из 7 ===
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
                encoder=far.AudioEncoder.AACLC
            )
        )
        audio_player = fta.Audio(autoplay=True, playback_rate=0.85)
        page.services.extend([recorder, audio_player])
        page.update()

        is_recording = False
        temp_audio_path = os.path.join(STORAGE_DIR, "temp_recording.m4a")

        mic_button = ft.IconButton(
            icon=ft.Icons.MIC_ROUNDED,
            icon_color=ft.Colors.BLUE_600,
            tooltip="Голосовой ввод",
        )

        async def mic_button_click(e):
            nonlocal is_recording
            stt_mode = get_setting("stt_mode").strip() or "Средствами телефона (Android)"

            if stt_mode == "Средствами телефона (Android)":
                try:
                    has_perm = await recorder.has_permission()
                    if not has_perm:
                        show_snack("Выдайте разрешение на микрофон и нажмите кнопку снова", ft.Colors.ORANGE_700, duration=2000)
                        return
                    mic_button.icon = ft.Icons.HEARING_ROUNDED
                    mic_button.icon_color = ft.Colors.RED_400
                    page.update()
                    show_snack("Говорите на английском...", ft.Colors.GREEN_700, duration=1500)

                    text = await recognize_audio_async("")
                    mic_button.icon = ft.Icons.MIC_ROUNDED
                    mic_button.icon_color = ft.Colors.BLUE_600
                    page.update()

                    if text:
                        user_input.value = text
                        page.update()
                        await send_message()
                    else:
                        show_snack("Речь не распознана.", ft.Colors.ORANGE_700, duration=1500)
                except Exception as ex:
                    mic_button.icon = ft.Icons.MIC_ROUNDED
                    mic_button.icon_color = ft.Colors.BLUE_600
                    page.update()
                    show_snack(str(ex), ft.Colors.RED_600, duration=3000)
                return

            if is_recording:
                try:
                    recorded_path = await recorder.stop_recording()
                    is_recording = False
                    mic_button.icon = ft.Icons.MIC_ROUNDED
                    mic_button.icon_color = ft.Colors.BLUE_600
                    loading_indicator.visible = True
                    page.update()
                    show_snack("Распознаю речь...", ft.Colors.BLUE_700, duration=1500)

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
                    show_snack(f"Ошибка распознавания: {ex}", ft.Colors.RED_600, duration=2000)
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
                    show_snack("Идет запись... Нажмите красную кнопку для отправки.", ft.Colors.GREEN_700, duration=500)
                except Exception as ex:
                    is_recording = False
                    mic_button.icon = ft.Icons.MIC_ROUNDED
                    mic_button.icon_color = ft.Colors.BLUE_600
                    page.update()
                    show_snack(f"Ошибка микрофона: {ex}", ft.Colors.RED_600, duration=1000)

        mic_button.on_click = lambda e: page.run_task(mic_button_click, e)

# === ФРАГМЕНТ 6 из 7 ===
        async def speak_text(english_text: str):
            clean_text = extract_english_part(english_text)
            if not clean_text:
                return

            tts_mode = get_setting("tts_mode").strip() or "Бесплатный голос (Android TTS)"

            if tts_mode == "Бесплатный голос (Android TTS)":
                try:
                    await asyncio.to_thread(_sync_android_tts, clean_text)
                except Exception as e:
                    chat_list.controls.append(build_message_bubble("error", f"🔊 {e}"))
                    page.update()
                return

            try:
                b64_audio = await get_tts_base64_async(clean_text)
                tts_path = os.path.join(STORAGE_DIR, f"tts_{int(time.time() * 1000)}.mp3")
                with open(tts_path, "wb") as f:
                    f.write(base64.b64decode(b64_audio))

                for fname in os.listdir(STORAGE_DIR):
                    if fname.startswith("tts_") and fname.endswith(".mp3") and os.path.join(STORAGE_DIR, fname) != tts_path:
                        try:
                            os.remove(os.path.join(STORAGE_DIR, fname))
                        except Exception:
                            pass

                played_native = await asyncio.to_thread(_play_mp3_android_native, tts_path)
                if not played_native:
                    audio_player.src = f"file://{tts_path}"
                    audio_player.autoplay = True
                    audio_player.playback_rate = 0.85
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

# === ФРАГМЕНТ 8 (часть 1 из бывшего 7-го) ===
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

# === ФРАГМЕНТ 9 (часть 2 из бывшего 7-го) ===
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
            value=get_setting("tts_mode") or "Бесплатный голос (Android TTS)",
            options=[
                ft.dropdown.Option("Бесплатный голос (Android TTS)"),
                ft.dropdown.Option("Голос ИИ (OpenAI TTS)"),
            ]
        )

        stt_mode_dd = ft.Dropdown(
            label="Распознавание голоса (микрофон)",
            value=get_setting("stt_mode") or "Средствами телефона (Android)",
            options=[
                ft.dropdown.Option("Средствами телефона (Android)"),
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
                set_setting("tts_mode", tts_mode_dd.value or "Бесплатный голос (Android TTS)")
                set_setting("stt_mode", stt_mode_dd.value or "Средствами телефона (Android)")
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
