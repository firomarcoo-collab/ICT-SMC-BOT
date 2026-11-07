# ICT-SMC-BOT

الملفات:

- `ICT_SMC_AutoTrader.mq5`: البوت الأصلي لمنصة MetaTrader 5 (يعمل على ويندوز فقط).
- `ict_smc_bot.py`: نسخة Python بنفس المنطق، تعمل على أندرويد عبر Termux وتتصل بحساب MT5 من خلال خدمة MetaApi.
- `test_logic.py`: اختبارات للمنطق تعمل بدون إنترنت.

## التشغيل على أندرويد (Termux)

1. ثبّت Termux من F-Droid (نسخة Google Play قديمة ولا تُحدَّث).
2. من الإعدادات: Battery ← Termux ← Unrestricted، حتى لا يوقفه أندرويد في الخلفية. ويُفضَّل استخدام `termux-wake-lock`.
3. داخل Termux:

```
pkg update && pkg install python git
git clone https://github.com/firomarcoo-collab/ICT-SMC-BOT
cd ICT-SMC-BOT
pip install -r requirements.txt
```

4. أنشئ حساباً في MetaApi (metaapi.cloud) وأضف حساب MT5 (ديمو أولاً) وانسخ:
   - API token
   - Account id
5. شغّل البوت:

```
termux-wake-lock
export METAAPI_TOKEN="ضع_التوكن_هنا"
export METAAPI_ACCOUNT_ID="ضع_رقم_الحساب_هنا"
export SYMBOL="EURUSD"      # اكتبه كما يكتبه وسيطك تماماً، مثل EURUSDm
python ict_smc_bot.py
```

بشكل افتراضي يعمل البوت في وضع `DRY_RUN=1`: يسجّل الإشارات في الشاشة فقط ولا يرسل أوامر. راقبه عدة أيام، وإذا ظهرت الإشارات منطقية على الشارت شغّله بـ `DRY_RUN=0` على حساب ديمو.

## اختبار المنطق

```
python test_logic.py
```

## ملاحظات

- النسخة لم تُجرَّب مع MetaApi الحقيقي بعد، فجرّبها على ديمو فقط. فحصت منطق الإشارات وحساب الحجم بالاختبارات المرفقة.
- المنطق مطابق للنسخة الأصلية، ومنه أن أمر الـ Limit لا ينتهي ما دام معلقاً، وأن البوت لا يفتح إشارة جديدة على الرمز نفسه طالما هناك صفقة أو أمر معلّق له.
- إن لم يكن الرمز بعملة ربح تطابق عملة الحساب، يحاول البوت التحويل بزوج عملات، وإن لم يجده يتخطى الإشارة ويكتب تحذيراً.
- تثبيت `metaapi-cloud-sdk` على Termux قد يفشل في بعض المكتبات التي تحتاج بناءً؛ إن حدث ذلك أرسل نص الخطأ.
