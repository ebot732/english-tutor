import os
import re
import json
import sqlite3
import tempfile
import asyncio
import traceback
import urllib.request
import urllib.parse
import urllib.error
import flet as ft
import flet_audio as fta

STORAGE_DIR = os.getenv("FLET_APP_STORAGE_DATA") or tempfile.gettempdir()
DB_PATH = os.path.join(STORAGE_DIR, "tutor_data.db")


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
        "provider": "OpenRouter (Бесплатно)",
        "api_key": "",
        "level": "A1-A2 (Начальный)",
        "topic": "Путешествия и кафе",
        "auto_tts": "1"
    }
    for k, v in defaults.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()


def get_setting(key: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return str(row) if row and row is not None else ""


def set_setting(key: str, value: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


def get_ai_config() -> tuple[str, str, str]:
    provider = get_setting("provider")
    api_key = get_setting("api_key").strip()

    if provider == "ProxyAPI":
        return "https://api.proxyapi.ru/openai/v1", api_key, "gpt-4o-mini"
    elif provider == "VseGPT":
        return "https://api.vsegpt.ru/v1", api_key, "openai/gpt-4o-mini"
    else:
        return "https://openrouter.ai/api/v1", api_key, "openrouter/free"


def _sync_ask_llm(messages: list[dict], temperature: float) -> str:
    base_url, api_key, model = get_ai_config()
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com",
        "X-Title": "EnglishTutorApp",
        "User-Agent": "EnglishTutorApp/1.0"
    }
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            if "error" in data:
                raise RuntimeError(str(data["error"]))
            return data["choices"]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Ошибка сервера {e.code}: {err_body[:200]}")


async def ask_llm(messages: list[dict], temperature: float = 0.7) -> str:
    return await asyncio.to_thread(_sync_ask_llm, messages, temperature)


def _sync_download_tts(clean_text: str, out_path: str):
    q = urllib.parse.quote(clean_text)
    url = f"https://google.com{q}&tl=en&client=tw-ob"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        with open(out_path, "wb") as f:
            f.write(resp.read())


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
##########
async def main(page: ft.Page):
    try:
        init_db()

        page.title = "English AI Tutor"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.padding = 0
        page.bgcolor = "#F4F6F9"

        def show_snack(msg: str, color: str = ft.Colors.BLUE_700):
            try:
                page.open(ft.SnackBar(ft.Text(msg, color=ft.Colors.WHITE), bgcolor=color))
            except Exception:
                page.snack_bar = ft.SnackBar(ft.Text(msg, color=ft.Colors.WHITE), bgcolor=color)
                page.snack_bar.open = True
                page.update()

        async def speak_text(english_text: str):
            clean_text = extract_english_part(english_text)
            if not clean_text:
                return
            try:
                tts_file = os.path.join(tempfile.gettempdir(), f"tts_{abs(hash(clean_text))}.mp3")
                if not os.path.exists(tts_file):
                    await asyncio.to_thread(_sync_download_tts, clean_text, tts_file)

                for item in list(page.overlay):
                    if isinstance(item, fta.Audio):
                        page.overlay.remove(item)

                player = fta.Audio(src=tts_file, autoplay=True)
                page.overlay.append(player)
                page.update()
            except Exception as e:
                show_snack(f"Ошибка озвучки: {e}", ft.Colors.RED_600)

        chat_list = ft.ListView(expand=True, spacing=12, padding=15, auto_scroll=True)
        user_input = ft.TextField(
            hint_text="Напишите или надиктуйте на EN...",
            expand=True,
            border_radius=20,
            bgcolor=ft.Colors.WHITE,
            content_padding=ft.padding.symmetric(horizontal=16, vertical=10),
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
                translation = "Перевод не получен (проверьте API)"

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
                    ft.ElevatedButton(
                        text=f"+ {w}",
                        icon=ft.Icons.BOOKMARK_ADD_OUTLINED,
                        style=ft.ButtonStyle(
                            padding=ft.padding.symmetric(horizontal=10, vertical=4),
                            bgcolor=ft.Colors.AMBER_100,
                            color=ft.Colors.BLACK87,
                        ),
                        on_click=lambda e, word=w: page.run_task(save_word_to_dict, word)
                    )
                )

            bubble_controls = [
                ft.Text(
                    content.replace("**", ""),
                    color=ft.Colors.WHITE if (is_user or is_error) else ft.Colors.BLACK87,
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
                        shadow=ft.BoxShadow(blur_radius=4, color=ft.Colors.BLACK12)
                    )
                ],
                alignment=ft.MainAxisAlignment.END if is_user else ft.MainAxisAlignment.START
            )
##########
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

        async def mic_button_click(e):
            await user_input.focus()
            show_snack("Нажмите значок микрофона на открывшейся клавиатуре телефона (язык EN)!", ft.Colors.BLUE_700)

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
                    padding=ft.padding.symmetric(horizontal=16, vertical=8),
                    bgcolor=ft.Colors.WHITE
                ),
                loading_indicator,
                chat_list,
                ft.Container(
                    content=ft.Row(
                        [
                            ft.IconButton(
                                icon=ft.Icons.MIC_ROUNDED,
                                icon_color=ft.Colors.BLUE_600,
                                tooltip="Голосовой ввод с клавиатуры",
                                on_click=lambda e: page.run_task(mic_button_click, e)
                            ),
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
                    padding=ft.padding.symmetric(horizontal=16, vertical=14),
                    bgcolor=ft.Colors.WHITE,
                    width=float("inf")
                ),
                dict_list
            ],
            expand=True,
            spacing=0,
            visible=False
        )
########
        provider_dd = ft.Dropdown(
            label="Провайдер ИИ",
            value=get_setting("provider") or "OpenRouter (Бесплатно)",
            options=[
                ft.dropdown.Option("OpenRouter (Бесплатно)"),
                ft.dropdown.Option("ProxyAPI"),
                ft.dropdown.Option("VseGPT"),
            ]
        )

        api_key_tf = ft.TextField(
            label="API-ключ",
            value=get_setting("api_key"),
            password=True,
            can_reveal_password=True,
            hint_text="Вставьте ключ от OpenRouter, ProxyAPI или VseGPT"
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

        auto_tts_sw = ft.Switch(
            label="Автоматически озвучивать ответы учителя",
            value=(get_setting("auto_tts") == "1")
        )

        save_status_text = ft.Text("", color=ft.Colors.GREEN_700, weight=ft.FontWeight.BOLD)

        def save_settings_click(e):
            try:
                set_setting("provider", provider_dd.value or "OpenRouter (Бесплатно)")
                set_setting("api_key", api_key_tf.value or "")
                set_setting("level", level_dd.value or "A1-A2 (Начальный)")
                set_setting("topic", topic_tf.value or "Путешествия и кафе")
                set_setting("auto_tts", "1" if auto_tts_sw.value else "0")
                save_status_text.value = "✅ Настройки сохранены в память телефона!"
                save_status_text.color = ft.Colors.GREEN_700
                page.update()
                show_snack("Настройки успешно сохранены!", ft.Colors.GREEN_700)
            except Exception as ex:
                save_status_text.value = f"❌ Ошибка сохранения: {ex}"
                save_status_text.color = ft.Colors.RED_600
                page.update()

        settings_view = ft.Column(
            [
                ft.Container(
                    content=ft.Text("Настройки обучения и ИИ", size=18, weight=ft.FontWeight.BOLD),
                    padding=ft.padding.symmetric(horizontal=16, vertical=14),
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
                                "Бесплатный ключ (без карты) берется на openrouter.ai/settings/keys. "
                                "Для РФ без VPN подходят ProxyAPI и VseGPT.",
                                size=12,
                                color=ft.Colors.GREY_700
                            ),
                            ft.Divider(),
                            level_dd,
                            topic_tf,
                            auto_tts_sw,
                            ft.ElevatedButton(
                                "Сохранить настройки",
                                icon=ft.Icons.SAVE,
                                bgcolor=ft.Colors.BLUE_600,
                                color=ft.Colors.WHITE,
                                width=float("inf"),
                                height=48,
                                on_click=save_settings_click
                            ),
                            save_status_text
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
    ft.app(target=main)
