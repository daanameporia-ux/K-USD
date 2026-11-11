# Расширение регистрации (KYC)

Сейчас регистрация — заглушка: `StubKycProvider` (`backend/app/kyc/stub.py`) сразу возвращает «пройдено». Блок построен так, чтобы многошаговую анкету можно было добавить, не трогая остальной код.

## Как это устроено

- Интерфейс — `KycProvider` в `backend/app/kyc/base.py`:
  - `start(user) -> KycStep` — первый шаг после нажатия «Пройти регистрацию»;
  - `handle_input(user, data: KycInput) -> KycStep` — обработка ответа пользователя;
  - `is_passed(user) -> bool`.
- `KycStep.status`: `"passed"` (регистрация завершена), `"rejected"` (отказ, показать `message`), `"input_required"` (показать `message` и ждать ответа). `KycStep.data` дописывается в `users.kyc_data` (jsonb).
- `KycInput` приходит из бота (`text` — текст сообщения) или из Mini App (`data` — поля формы).
- Применяет шаги `services/users.py` (`start_registration`, `registration_input`): при `passed` клиент получает `status=active`, `kyc_status=passed`, наценку по умолчанию и `registered_at`.
- В боте шаги показываются хендлерами `reg:start` и состояния `kyc:input` (`backend/app/telegram/handlers/common.py`); в Mini App — `POST /api/client/register` (без тела — старт, с `{"text": ..., "data": {...}}` — ответ на шаг).
- Весь клиентский функционал закрыт проверкой `status == active and kyc_status == passed` (`users.is_active`, `users.ensure_active`).

## Пример: анкета из двух вопросов

```python
class QuestionnaireKyc:
    async def start(self, user):
        return KycStep("input_required", "Как вас зовут (ФИО)?", expects="text", data={"step": "name"})

    async def handle_input(self, user, data):
        step = (user.kyc_data or {}).get("step")
        if step == "name":
            return KycStep("input_required", "Укажите номер телефона", expects="phone",
                           data={"step": "phone", "name": data.text})
        if step == "phone":
            return KycStep("passed", data={"step": "done", "phone": data.text})
        return await self.start(user)

    async def is_passed(self, user):
        return user.kyc_status == KycStatus.passed
```

Подключение — одна строка в `backend/app/container.py`: `self.kyc = QuestionnaireKyc()`.

Для внешнего KYC-сервиса (ссылка на прохождение проверки у партнёра) `start` возвращает `input_required` с сообщением и ссылкой, а результат приходит webhook-ом: добавьте эндпоинт, который проверяет подпись партнёра и вызывает `users.registration_input` с данными результата.

В Mini App экран «Регистрация» (`frontend/client/src/screens/Register.tsx`) показывает `step.message`; для анкеты добавьте поле ввода и отправку `{"text": ...}`.
