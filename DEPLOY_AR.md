# تشغيل Kenan Trader AI على الهاتف (Termux)

## أول مرة أو بعد أي تحديث
```
cd ~/ICT-SMC-BOT
git pull
pip install tzdata
```
(الأمر الأخير يضمن أن توقيت نيويورك صحيح في الصيف. إذا ظهر أنه مثبّت فلا بأس.)

## التشغيل
اضغط اختصار Termux:Widget `ICT-Alerts`، أو:
```
cd ~/ICT-SMC-BOT && PROFILE=all python ict_alerts.py
```

## ما الذي يظهر في السطر
- `[NY: ... , New York open]`: سيناريو جلسة نيويورك (للعرض فقط).
- `[state: ...]`: حالة السوق (للعرض فقط).
- في الإشارة: RSI وMACD وTP1 إلى TP5 (للعرض)؛ الإشارة الفعلية هي `ENTRY CONFIRMED` وحدها.
- تنبيهات المراقبة (اقتراب من المنطقة، دخولها، المرحلة 3، إعادة الاختبار، الإلغاء) تقول دائماً إنها ليست إشارة دخول.

## مفاتيح تحكم (اختيارية، تُضاف قبل `python`)
| المفتاح | الأثر |
|---|---|
| `MIN_NOTIFY_SCORE=80` | حد التنبيه (الافتراضي 70؛ السكالب 85) |
| `HTF_MODE=default` | الرجوع إلى قواعد H4/H1/M15 بدل H1 |
| `NY_SESSION=0` / `MARKET_STATE=0` | إخفاء عرض نيويورك / حالة السوق |
| `ALERT_CLASSES=0` / `ALERT_VOICE=0` | إيقاف تنبيهات المراقبة / الصوت |
| `SIGNAL_LEVEL=balanced` | إشارات أكثر قليلاً |

## قياس الأداء
```
python journal.py            # حسب السوق والجلسة والبروفايل
python journal.py session
```
أقل من 30 صفقة = عينة صغيرة لا تكفي للحكم.

## الاختبارات الرجعية (تحتاج ملفات بيانات)
```
python fetch_binance_history.py --symbol BTCUSDT --days 365 --out BTCUSDT_M5.csv
python fetch_dukascopy_history.py --symbol XAUUSD --days 90 --out XAUUSD_M5.csv
```
ثم أرسل الملفين في المحادثة.

## تذكير
البوت إشارات فقط ولا ينفذ صفقات. الدرجة تقييم فني وليست احتمال ربح. الفرع الحالي `phase2-restart-persistence` غير مدموج في `main`.
