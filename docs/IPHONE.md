# iPhone → JARVIS

Нужен доступный по HTTPS backend и значение JARVIS_API_KEY. Локальный адрес localhost на iPhone
указывает на сам телефон, поэтому используйте публичный домен Railway/VPS.

Создайте в приложении «Команды» Shortcut с названием «Джарвис»:

1. **Диктовать текст** (Dictate Text), язык русский. Сохраните результат в переменную CommandText.
2. **Сгенерировать UUID** (Generate UUID). Сохраните в RequestId один раз на запуск.
3. **Получить содержимое URL** (Get Contents of URL): `https://ВАШ-ДОМЕН/command`.
   Метод POST; заголовки `Authorization: Bearer ВАШ_JARVIS_API_KEY` и `Content-Type: application/json`.
   Тело запроса JSON: `request_id` = RequestId, `text` = CommandText.
4. Сохраните ответ как Response. Получите из словаря Response ключ `status`.
5. Если `status` = `confirmation_required`:
   - Сохраните `confirmation_token` из **исходного** Response в переменную ConfirmationToken.
   - Покажите весь словарь `preview` через «Быстрый просмотр»/«Показать результат».
     Для письма должны быть видны получатель, тема и полный текст; для удаления — action и resource_id.
   - «Выбрать из меню»: **Подтвердить** / **Отменить**. Не выбирайте подтверждение автоматически.
   - В ветке Подтвердить выполните POST `https://ВАШ-ДОМЕН/confirm` с теми же заголовками,
     JSON `token` = ConfirmationToken, `approve` = логическое **true**, не строка.
   - В ветке Отменить выполните такой же запрос с логическим **false**.
   - Сохраните ответ подтверждения/отмены как Response.
6. Получите ключ `reply` из Response и передайте в **Произнести текст** (Speak Text).
7. Если API вернул ошибку (`detail`, а не `reply`), покажите detail и завершите Shortcut.
   Не произносите «готово» при HTTP-ошибке. Не повторяйте запись с новым UUID после таймаута.

Пример команды: «завтра в 11 напомни проверить trading bot».
Для структурированного письма сделайте отдельный Shortcut с запросом получателя/темы/текста и полем
`action=gmail.send`; остальные шаги, включая ручное подтверждение, такие же.

В случае потерянного ответа: GET `https://ВАШ-ДОМЕН/requests/RequestId` с тем же Bearer.
`state=completed` содержит сохранённый ответ. `uncertain`/`running` требует проверки целевого сервиса;
не запускайте ещё одно отправление. При повторе POST /command сохраняйте исходное тело и RequestId.

Добавьте Shortcut на домашний экран или назначьте Action Button. Siri: «Запусти Джарвис».
В Google Calendar на iPhone включите уведомления и выбранный календарь: уведомление создаёт Google,
backend не должен оставаться открытым на телефоне. Голос распознаётся средствами iOS; сервер принимает текст.

Готовый подписанный `.shortcut`/iCloud-link здесь не создан: настройка выполняется в вашем приложении
«Команды», где хранится ваш API key. Не делитесь Shortcut, содержащим реальный ключ.

Официальная справка: [Shortcuts User Guide](https://support.apple.com/guide/shortcuts/welcome/ios).
