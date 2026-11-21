import flet as ft
import flet_audio_recorder as far
import flet_audio as fa
from openai import OpenAI
import json
import os
import re

SYSTEM_PROMPT = """
Ты — дружелюбный репетитор английского языка. Твой ученик имеет начальный уровень (Beginner / A1).
Веди с ним простой диалог на тему АВТОМОБИЛЕЙ (покупка машины, автосервис, заправка, устройство авто, вождение).
Правила:
1. Пиши короткие и простые фразы на английском (1-2 предложения), используй базовую лексику (car, wheel, engine, gas, drive, repair, fast, red).
2. После своей реплики на английском, с новой строки в скобках дай её перевод на русский.
3. Если ученик допустил ошибку в грамматике, выборе слов или произношении (судя по транскрибации), сначала мягко поправь его на русском языке, объясни как правильно, а затем продолжи диалог.
4. Всегда заканчивай свой ответ простым вопросом, чтобы ученик мог продолжить разговор.
"""

def main(page: ft.Page):
    page.title = "Auto English AI"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 10

    # Загружаем сохраненные настройки и словарь из памяти телефона
    saved_key = page.client_storage.get("api_key") or ""
    saved_url = page.client_storage.get("base_url") or "https://proxyapi.ru"
    vocab_list = page.client_storage.get("vocab") or [
        {"en": "Engine", "ru": "Двигатель"},
        {"en": "Steering wheel", "ru": "Руль"},
        {"en": "Gas station", "ru": "Заправка"}
    ]

    messages_history = [{"role": "system", "content": SYSTEM_PROMPT}]
    audio_rec_path = "user_voice.m4a"
    tts_audio_path = "tutor_voice.mp3"

    def get_client():
        key = page.client_storage.get("api_key")
        url = page.client_storage.get("base_url") or "https://proxyapi.ru"
        if not key:
            return None
        return OpenAI(api_key=key, base_url=url)

    # --- АУДИОПЛЕЕР ДЛЯ ОЗВУЧКИ УЧИТЕЛЯ ---
    audio_player = fa.Audio(autoplay=True)
    page.overlay.append(audio_player)

    def speak_text(full_text: str):
        client = get_client()
        if not client:
            return
        # Вырезаем русский перевод в скобках, чтобы озвучивать только английский текст
        en_only = re.sub(r'\(.*?\)', '', full_text).strip()
        if not en_only:
            en_only = full_text

        status_text.value = "🔊 Генерирую голос учителя..."
        page.update()
        try:
            response = client.audio.speech.create(
                model="tts-1",
                voice="alloy",  # Приятный нейтральный голос (можно выбрать nova, echo, shimmer)
                input=en_only
            )
            response.stream_to_file(tts_audio_path)
            audio_player.src = tts_audio_path
            audio_player.update()
            audio_player.play()
            status_text.value = "Готово! Ваша очередь."
        except Exception as e:
            status_text.value = f"Ошибка озвучки: {e}"
        page.update()

    # --- ДИАЛОГ БЫСТРОГО ДОБАВЛЕНИЯ В СЛОВАРЬ ---
    quick_en = ft.TextField(label="Слово на английском")
    quick_ru = ft.TextField(label="Перевод на русский")

    def save_quick_word(e):
        if quick_en.value.strip() and quick_ru.value.strip():
            vocab_list.insert(0, {"en": quick_en.value.strip(), "ru": quick_ru.value.strip()})
            page.client_storage.set("vocab", vocab_list)
            refresh_vocab_ui()
            quick_en.value = ""
            quick_ru.value = ""
            page.close(quick_dialog)
            page.snack_bar = ft.SnackBar(ft.Text("Слово сохранено в словарь!"))
            page.snack_bar.open = True
            page.update()

    quick_dialog = ft.AlertDialog(
        title=ft.Text("Добавить слово в словарь"),
        content=ft.Column([quick_en, quick_ru], tight=True),
        actions=[
            ft.TextButton("Отмена", on_click=lambda e: page.close(quick_dialog)),
            ft.ElevatedButton("Сохранить", on_click=save_quick_word)
        ]
    )

    # --- ВКЛАДКА 1: ЧАТ-ТРЕНАЖЕР ---
    chat_list = ft.ListView(expand=True, spacing=10, auto_scroll=True)
    status_text = ft.Text("Нажмите микрофон и говорите...", color=ft.Colors.GREY_600, size=12)

    def add_message(sender: str, text: str, is_user: bool):
        controls = [
            ft.Text(sender, weight=ft.FontWeight.BOLD, size=12,
                    color=ft.Colors.BLUE_700 if not is_user else ft.Colors.GREEN_700),
            ft.Text(text, selectable=True, size=15)
        ]
        # Кнопки озвучки и сохранения слова добавляем к сообщениям учителя
        if not is_user:
            controls.append(
                ft.Row([
                    ft.TextButton("🔊 Прослушать", on_click=lambda e, t=text: speak_text(t)),
                    ft.TextButton("➕ В словарь", on_click=lambda e: page.open(quick_dialog))
                ])
            )

        chat_list.controls.append(
            ft.Container(
                content=ft.Column(controls, spacing=5),
                bgcolor=ft.Colors.BLUE_50 if not is_user else ft.Colors.GREEN_50,
                padding=12,
                border_radius=12,
            )
        )
        page.update()

    def ask_tutor(user_text: str):
        client = get_client()
        if not client:
            add_message("Система:", "⚠️ Сначала введите API-ключ во вкладке «Настройки»!", is_user=False)
            return

        status_text.value = "Учитель думает..."
        page.update()
        messages_history.append({"role": "user", "content": user_text})
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages_history,
                temperature=0.7
            )
            reply = response.choices.message.content
            messages_history.append({"role": "assistant", "content": reply})
            add_message("Учитель (Auto AI):", reply, is_user=False)
        except Exception as e:
            add_message("Ошибка API:", str(e), is_user=False)

        status_text.value = "Готово! Ваша очередь говорить."
        page.update()

    def process_audio():
        client = get_client()
        if not client:
            return
        status_text.value = "Проверяю ваше произношение..."
        page.update()
        try:
            with open(audio_rec_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="en",
                    prompt="Transcribe literally what the beginner student says about cars, keeping mispronunciations."
                )
            recognized_text = transcript.text
            add_message("Вы сказали:", recognized_text, is_user=True)
            ask_tutor(f"Ученик сказал вслух (распознано ИИ): '{recognized_text}'. Проверь ошибки и ответь.")
        except Exception as e:
            status_text.value = f"Ошибка микрофона/API: {e}"
            page.update()

    audio_recorder = far.AudioRecorder(audio_encoder=far.AudioEncoder.AAC)
    page.overlay.append(audio_recorder)
    is_recording = False

    def toggle_record(e):
        nonlocal is_recording
        if not is_recording:
            audio_recorder.start_recording(audio_rec_path)
            is_recording = True
            mic_btn.text = "⏹ Остановить и отправить"
            mic_btn.bgcolor = ft.Colors.RED_600
            status_text.value = "Идет запись... Говорите на английском!"
        else:
            audio_recorder.stop_recording()
            is_recording = False
            mic_btn.text = "🎤 Говорить (Запись)"
            mic_btn.bgcolor = ft.Colors.BLUE_600
            process_audio()
        page.update()

    text_input = ft.TextField(hint_text="Или напишите ответ...", expand=True)

    def send_text(e):
        if text_input.value.strip():
            msg = text_input.value
            text_input.value = ""
            add_message("Вы написали:", msg, is_user=True)
            ask_tutor(msg)

    mic_btn = ft.ElevatedButton(
        "🎤 Говорить (Запись)",
        on_click=toggle_record,
        bgcolor=ft.Colors.BLUE_600,
        color=ft.Colors.WHITE,
        height=48,
        expand=True
    )

    chat_tab = ft.Column([
        chat_list,
        status_text,
        ft.Row([mic_btn]),
        ft.Row([text_input, ft.IconButton(ft.Icons.SEND, on_click=send_text)])
    ], expand=True)

    # --- ВКЛАДКА 2: СЛОВАРЬ ---
    vocab_col = ft.ListView(expand=True, spacing=8)
    new_en_input = ft.TextField(label="Английское слово", expand=True)
    new_ru_input = ft.TextField(label="Перевод", expand=True)

    def delete_word(idx):
        vocab_list.pop(idx)
        page.client_storage.set("vocab", vocab_list)
        refresh_vocab_ui()

    def refresh_vocab_ui():
        vocab_col.controls.clear()
        for i, item in enumerate(vocab_list):
            vocab_col.controls.append(
                ft.Card(
                    content=ft.ListTile(
                        title=ft.Text(item["en"], weight=ft.FontWeight.BOLD),
                        subtitle=ft.Text(item["ru"]),
                        trailing=ft.Row([
                            ft.IconButton(ft.Icons.VOLUME_UP, on_click=lambda e, w=item["en"]: speak_text(w)),
                            ft.IconButton(ft.Icons.DELETE_OUTLINE, on_click=lambda e, idx=i: delete_word(idx))
                        ], tight=True)
                    )
                )
            )
        page.update()

    def add_vocab_word(e):
        if new_en_input.value.strip() and new_ru_input.value.strip():
            vocab_list.insert(0, {"en": new_en_input.value.strip(), "ru": new_ru_input.value.strip()})
            page.client_storage.set("vocab", vocab_list)
            new_en_input.value = ""
            new_ru_input.value = ""
            refresh_vocab_ui()

    refresh_vocab_ui()

    vocab_tab = ft.Column([
        ft.Text("Мой словарь автолюбителя", size=18, weight=ft.FontWeight.BOLD),

# https://www.google.com/m?q=%D0%9D%D1%83%D0%B6%D0%BD%D0%BE+%D1%81%D0%BE%D0%B7%D0%B4%D0%B0%D1%82%D1%8C+%D0%BF%D1%80%D0%B8%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D0%B5+%D0%B4%D0%BB%D1%8F+%D0%B0%D0%BD%D0%B4%D1%80%D0%BE%D0%B8%D0%B4+%D0%B2+%D0%B2%D0%B8%D0%B4%D0%B5+apk.+%D0%9E%D0%B1%D1%83%D1%87%D0%B0%D1%8E%D1%89%D0%B5%D0%B5+%D0%B0%D0%BD%D0%B3%D0%BB%D0%B8%D0%B9%D1%81%D0%BA%D0%BE%D0%BC%D1%83+%D1%8F%D0%B7%D1%8B%D0%BA%D1%83.%0A%D0%92+%D0%BD%D0%B5%D0%BC+%D0%B1%D1%83%D0%B4%D0%B5%D1%82+%D1%81%D0%BB%D0%BE%D0%B2%D0%B0%D1%80%D1%8C+%D0%B0%D0%BD%D0%B3%D0%BB%D0%B8%D0%B9%D1%81%D0%BA%D0%B8%D1%85+%D1%81%D0%BB%D0%BE%D0%B2+%D0%B8+%D0%BF%D1%80%D0%B5%D0%B4%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D0%B9+%28%D1%81+%D0%B2%D0%BE%D0%B7%D0%BC%D0%BE%D0%B6%D0%BD%D0%BE%D1%81%D1%82%D1%8C%D1%8E+%D0%B4%D0%BE%D0%B1%D0%B0%D0%B2%D0%BB%D0%B5%D0%BD%D0%B8%D1%8F+%D0%BD%D0%BE%D0%B2%D1%8B%D1%85+%D1%81%D0%BB%D0%BE%D0%B2+%D0%B8+%D1%82%D0%B4%29.%0A%D0%97%D0%B0+%D1%83%D1%80%D0%BE%D0%BA+%D0%B1%D1%83%D0%B4%D0%B5%D1%82+%D0%BF%D1%80%D0%B5%D0%B4%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%BE+10+%D0%BD%D0%BE%D0%B2%D1%8B%D1%85+%D1%81%D0%BB%D0%BE%D0%B2%2C+%D0%B8%D1%85+%D0%BF%D0%B5%D1%80%D0%B5%D0%B2%D0%BE%D0%B4+%D0%BD%D0%B0+%D1%80%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9%2C+%D0%B8+%D0%BF%D1%80%D0%BE%D0%B2%D0%B5%D1%80%D0%BA%D0%B0+%D0%B7%D0%B2%D1%83%D1%87%D0%B0%D0%BD%D0%B8%D1%8F+%D0%BD%D0%B0+%D0%B0%D0%BD%D0%B3%D0%BB%D0%B8%D0%B9%D1%81%D0%BA%D0%BE%D0%BC%2C+%D0%BF%D1%80%D0%B8%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D0%B5+%D0%B4%D0%BE%D0%BB%D0%B6%D0%BD%D0%BE+%D0%BF%D1%80%D0%BE%D0%B2%D0%B5%D1%80%D1%8F%D1%82%D1%8C+%D0%BF%D1%80%D0%BE%D0%B8%D0%B7%D0%BD%D0%BE%D1%88%D0%B5%D0%BD%D0%B8%D0%B5+%D0%B8+%D0%BF%D0%BE%D0%BF%D1%80%D0%B0%D0%B2%D0%BB%D1%8F%D1%82%D1%8C.%0A%D0%90+%D1%82%D0%B0%D0%BA%D0%B6%D0%B5+%D0%B7%D0%B0%D0%B4%D0%B0%D0%B2%D0%B0%D1%82%D1%8C+%D0%B2%D0%BE%D0%BF%D1%80%D0%BE%D1%81%D1%8B+%D0%BF%D0%BE+%D1%83%D1%80%D0%BE%D0%BA%D1%83+%D0%B8+%D0%BF%D1%80%D0%BE%D0%B2%D0%B5%D1%80%D1%8F%D1%82%D1%8C+%D0%BE%D1%82%D0%B2%D0%B5%D1%82%D1%8B+%D0%BF%D0%B8%D1%81%D1%8C%D0%BC%D0%B5%D0%BD%D0%BD%D1%8B%D0%B5+%D0%B8+%D1%83%D1%81%D1%82%D0%BD%D1%8B%D0%B5.%0A%D0%90+%D1%82%D0%B0%D0%BA%D0%B6%D0%B5+%D0%BC%D0%BE%D0%B4%D0%B5%D0%BB%D0%B8%D1%80%D0%BE%D0%B2%D0%B0%D1%82%D1%8C+%D0%B4%D0%B8%D0%B0%D0%BB%D0%BE%D0%B3%D0%B8+%D1%81+%D0%BE%D0%B1%D1%83%D1%87%D0%B0%D1%8E%D1%89%D0%B8%D0%BC%D1%81%D1%8F.%0A%D0%9E%D0%B1%D1%8A%D1%8F%D1%81%D0%BD%D0%B8+%D0%BF%D0%BE%D1%88%D0%B0%D0%B3%D0%BE%D0%B2%D0%BE%2C+%D0%BA%D0%B0%D0%BA+%D1%8D%D1%82%D0%BE+%D0%BC%D0%BE%D0%B6%D0%BD%D0%BE+%D1%80%D0%B5%D0%B0%D0%BB%D0%B8%D0%B7%D0%BE%D0%B2%D0%B0%D1%82%D1%8C%2C+%D0%BD%D0%B0+%D0%BA%D0%B0%D0%BA%D0%BE%D0%BC+%D1%8F%D0%B7%D1%8B%D0%BA%D0%B5+%D0%BF%D1%80%D0%BE%D0%BD%D1%80%D0%B0%D0%BC%D0%BC%D0%B8%D1%80%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D1%8F%2C+%D0%B8%D1%81%D0%BF%D0%BE%D0%BB%D1%8C%D0%B7%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D1%8F+%D0%B8%D0%B8+%D0%B8+%D1%82%D0%B4&client=ms-opera-mobile&channel=new&espv=1&mstk=AUtExfDzi-8Ed2HbSEZDWsZcDi6QomXQiDZ277SmQX3iTzTZttK0X1Mz-JDGf5B5xsw1OTg9XeuGO2untuteuBEXtmd3fzWwE0Yabf3y9bfDy6qYwZvD1uFvb6WEH4pLemb-Hmrg01hkp1swtHxuZJzaQjFX1eCMwOMmnVrCTeCTwIWQKIye2MGRLeRIg_HMhdKewQ3-erklWt3LwpTYFhd4-bHG1PBR_Q&csuir=1&udm=50&aioh=3#aof=1
# Нужно создать приложение для андроид в виде apk. Обучающее английскому языку. В нем будет словарь английских слов и предложений (с возможностью добавления новых слов и тд). За урок будет предложено 10 новых слов, их перевод на русский, и проверка звучания на английском, приложение должно проверять произношение и поправлять. А также задавать вопросы по уроку и проверять ответы письменные и устные. А также моделировать диалоги с обучающимся. Объясни пошагово, как это можно реализовать, на каком языке пронраммирования, использования ии и тд