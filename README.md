# ICT-SMC-BOT

الملفات:

- `ICT_SMC_AutoTrader.mq5`: البوت الأصلي لمنصة MetaTrader 5 (يعمل على ويندوز فقط).
- `ict_alerts.py`: **وضع التنبيهات المجاني** على أندرويد (Termux). يراقب البتكوين والذهب وناسداك وداوجونز ويرسل لك الإشارة، وتضع الأمر يدوياً في MT5.
- `ict_smc_bot.py`: منطق الإشارات، ومعه نسخة تنفيذ آلي تعمل عبر MetaApi (خدمة **مدفوعة**).
- `classic.py`: التأكيدات الكلاسيكية (اتجاه EMA، فيبوناتشي، مستويات القمم والقيعان، شموع الابتلاع والانعكاس) وإشارة كلاسيكية مستقلة.
- `extras.py`: حاسبة اللوت، جلسات لندن ونيويورك، وتنبيهات الأخبار القوية.
- `markets.py` و`markets.json`: مواصفات كل سوق من وسيطك (يُملأ يدوياً)، ولا يُخمَّن شيء منها. افحص الحالة بـ`python markets.py`.
- `data_layer.py`: طبقة البيانات: فحص جودة الشموع (مفقودة، شاذة، مكررة، OHLC مستحيل، توقيت مستقبلي، قديمة)، وبصمة البيانات خلف كل إشارة، ومزوّد البيانات المجاني. مزوّد MT5 غير مكتوب لأنه يحتاج Windows.
- `structure.py`: محرك البنية والسيولة (BOS وCHoCH وMSS وPDH/PDL وPWH/PWL وآسيا وEqual H/L وسحب السيولة وPremium/Discount وOTE وفيبوناتشي وFVG وOB وBreaker وInducement) على شموع مغلقة فقط، وتعريفات كل مفهوم مكتوبة في أول الملف.
- `PLAN.md`: خطة التطوير إلى المحرك الاحترافي ومراحله وقيوده.
- `stats.py`: ملخص كل الصفقات المنتهية من `results.csv`.
- `tracker.py`: يتابع كل إشارة ويبلغك ربحت أم خسرت.
- `rct.py`: استراتيجية RCT (ارتداد واستمرار الاتجاه).
- `events.py`: تنبيهات مبكرة: سحب السيولة وFVG وOrder Block.
- `alert.wav`: نغمة التنبيه.
- `backtest.py`: يعيد تشغيل الإشارات على شموع سابقة ويحسب النتائج.
- `test_logic.py` و`test_rct.py` و`test_us_open.py` و`test_tracker.py` و`test_markets.py` و`test_structure.py` و`test_data_layer.py` و`test_events.py` و`test_extras.py` و`test_classic.py` و`test_alerts.py` و`test_backtest.py`: اختبارات تعمل بدون إنترنت.

## وضع التنبيهات على أندرويد (مجاني)

لا يحتاج أي تثبيت إضافي سوى Python.

```
pkg update && pkg install python git
git clone https://github.com/firomarcoo-collab/ICT-SMC-BOT
cd ICT-SMC-BOT
termux-wake-lock
python ict_alerts.py
```

لتحديث الملفات لاحقاً: `cd ICT-SMC-BOT` ثم `git pull`.

يطبع البوت سطراً لكل رمز كل 5 دقائق، مثل `BTCUSD: price 98000.00 ok`. وعند ظهور إشارة يطبع:

```
ICT SMC ALERT: BUY LIMIT BTCUSD
Entry / SL / TP / Risk distance
```

وتضع أنت أمر Buy Limit أو Sell Limit بهذه القيم في تطبيق MT5.

### اختيار الرموز

```
SYMBOLS=BTCUSD,XAUUSD python ict_alerts.py
```

الرموز المتاحة: `BTCUSD` و`XAUUSD` و`US100` (ناسداك) و`US30` (داوجونز) و`EURUSD` (يورو دولار، من Yahoo).

### مصادر الأسعار

- البتكوين: Binance (سعر فوري).
- الذهب: Yahoo Finance سعر فوري `XAUUSD=X`. ناسداك وداوجونز: مؤشرات `^NDX` و`^DJI` (أسعار فورية وليست عقوداً). وللعقود الآجلة: `FUTURES=1` (الكل) أو `FUTURES=US100,US30` (المؤشرات فقط، لأن المؤشرات الفورية لا تتحرك إلا في ساعات السوق الأمريكي، أما العقود فتتداول تقريباً 24 ساعة؛ السعر يختلف عن CFD لديك بفارق يحتاج `OFFSET_US100` وغيره).
- إذا ظهرت الرسالة `data delayed` والسوق مفتوح، فالمصدر متأخر عن السوق، فجرّب `FUTURES=1`.

هذه الأسعار قد تختلف عن أسعار وسيطك (JustMarkets لا يوفر مصدراً مجانياً يقرأه البوت). ولتقريب المستويات من وسيطك استخدم **الإزاحة**:

1. شغّل البوت بدون إزاحة، وانظر السعر الذي يطبعه لكل رمز، مثل `XAUUSD: price 4012.30 ok`.
2. قارنه بالسعر الحالي لنفس الرمز في MT5، وفرق السعر هو الإزاحة. مثال: MT5 يعرض 4000.10 والبوت 4012.30، فالإزاحة هي `-12.2`.
3. أعد التشغيل وفق الصيغة:
   ```
   OFFSET_XAUUSD=-12.2 OFFSET_US100=35 python ict_alerts.py
   ```
   فتخرج الأرقام الجديدة بأسعار وسيطك.

الفرق يتغير ببطء مع الوقت (وعند تبديل العقد الآجل)، فأعد حسابه كل بضعة أيام. وقبل وضع أي أمر قارن "Price now" بسعر MT5 للتأكد.

### الأسواق المغلقة

الذهب والمؤشرات مغلقة في عطلة نهاية الأسبوع، وسيطبع البوت `market closed or data delayed` لها. أما البتكوين فيعمل طوال الأسبوع، وهو مناسب للتجربة في أي وقت.

### تنبيه على الهاتف (اختياري)

- **إشعارات Termux:** ثبّت تطبيق Termux:API من F-Droid ثم `pkg install termux-api`. بعدها يرسل البوت إشعاراً بصوت عند كل إشارة.
- **نغمة خاصة:** الملف `alert.wav` (نغمة صاعدة قصيرة) يُشغَّل مع كل إشارة تلقائياً. لاستخدام نغمتك: `ALERT_SOUND=/path/to/file.mp3 python ict_alerts.py`، ولإيقاف النغمة الخاصة: `ALERT_SOUND=off`. للتجربة: `python -c "from ict_alerts import notify; notify('TEST')"`.
- **تيليجرام:** أنشئ بوتاً من BotFather ثم:
  ```
  export TELEGRAM_TOKEN="..."
  export TELEGRAM_CHAT_ID="..."
  ```

## حاسبة اللوت والجلسات والأخبار

- **اللوت:** ضع رصيدك فيظهر الحجم في كل تنبيه: `BALANCE=1000 python ict_alerts.py`. يُحسب من مسافة وقف الخسارة ومن **مواصفات وسيطك في `markets.json`** (حجم العقد وTick Value وأقل لوت وخطوته)، ويُقرَّب لأسفل. إن لم تُملأ المواصفات يكتب البوت `Lot: not calculated` ولا يخمّن. (خيار قديم: `CONTRACT_XAUUSD=100` يُستخدم كافتراض منك ويُوسم بذلك.)
- **الجلسات:** كل تنبيه يذكر الجلسة (لندن، نيويورك، أو تداخلهما) من 08:00 إلى 17:00 بتوقيت المدينة. `SESSION_ONLY=1` يمنع التنبيهات خارجها.
- **الأخبار:** يجلب البوت تقويم الأخبار المجاني كل ساعة، فيرسل تنبيهاً قبل الخبر القوي بـ30 دقيقة، ويضيف تحذيراً لأي إشارة قريبة من الخبر. `NEWS_BLOCK=1` يمنع الإشارات قربه، و`NEWS=0` يوقف الميزة.

## نتيجة الصفقات (ربح أو خسارة)

بعد كل إشارة دخول يتابعها البوت على الشموع التالية ويرسل لك تنبيهاً:

- `RESULT: WIN +2.0R`: وصل السعر إلى الهدف بعد أن دخل الصفقة.
- `RESULT: LOSS -1R`: ضرب وقف الخسارة.
- `NOT FILLED`: لم يصل السعر إلى الدخول خلال 24 شمعة، فألغِ الأمر المعلق في MT5.

يضيف أيضاً حساب **كل الصفقات**: `Total: 5 trades, 3 wins, 2 losses (win rate 60%, +4.0R)`، أي العدد والرابحة والخاسرة ونسبة الربح والمحصلة. وللتفصيل في أي وقت: `python stats.py` (الكل)، أو `python stats.py kind` (حسب الاستراتيجية)، أو `python stats.py symbol` (حسب الرمز)، أو `python stats.py timeframe` (حسب الفريم). وتُحفظ كل النتائج في ملف `results.csv` في مجلد البوت (R تعني مضاعف المخاطرة، و1R هو ما تخسره إذا ضرب الوقف).

- المحاكاة تفترض أن الأمر دخل لحظة لمس السعر ولا تشمل السبريد، وإذا لمست شمعة واحدة الوقف والهدف معاً تُحسب خسارة. والنتيجة الفعلية في MT5 قد تختلف قليلاً.
- المتابعة تعمل بالذاكرة: إذا أوقفت البوت تُنسى الصفقات المعلقة، أما النتائج المسجلة فتبقى.
- لإيقافها: `TRACK=0`.

## افتتاح السوق الأمريكي

أقوى الحركة غالباً في أول ساعة بعد افتتاح الأسهم الأمريكية (09:30 بتوقيت نيويورك، وهو 16:30 بتوقيت الأردن في الشتاء، وقد يكون 15:30 في الصيف).

- ينبهك البوت **قبل الافتتاح بـ15 دقيقة** وعند الافتتاح.
- كل إشارة تظهر من نصف ساعة قبل الافتتاح حتى ساعتين بعده يضاف لها سطر `US OPEN window`.
- `US_OPEN_ONLY=1`: لا يرسل إشارات الدخول إلا في هذه النافذة (التنبيهات المبكرة لا تتأثر).
- `US_OPEN_ALERTS=0` يوقف تنبيهات الافتتاح.
- الحركة السريعة تعني سبريد أوسع وانزلاقاً، فاحذر من الأوامر القريبة من الأخبار.

## الفريمات والسكالب والسوينغ

يحلل البوت **ستة فريمات**: 1 دقيقة و5 دقائق و15 دقيقة (**سكالب**)، وساعة و4 ساعات ويومي (**سوينغ**). كل تنبيه يحمل سطر `Timeframe: 5m (scalp)` ثم سطر `Trends:` فيه اتجاه كل فريم، مثل `Trends: 1d up | 4h up | 1h flat | 5m down`، ويقول هل الفريمات الأعلى توافق الصفقة أم تعاكسها. الصفقة مع الفريمات الأعلى أقوى عادةً.

- لتقليل الكثرة: `TIMEFRAMES=5m,15m,1h,4h python ict_alerts.py` (القائمة الكاملة 1m,5m,15m,1h,4h,1d).
- فريم الدقيقة كثير الإشارات الكاذبة، وأنصح بتجربته على الحساب التجريبي فقط.
- بيانات 4 ساعات للذهب والمؤشرات تُبنى من شموع الساعة لأن Yahoo لا يوفرها.
- التنبيهات المبكرة (سحب السيولة وFVG وOB) تعمل افتراضياً على 15m وما فوق: `EVENT_TIMEFRAMES=5m,15m,1h`.

## RCT: ارتداد واستمرار الاتجاه

- **استمرار (Continuation):** اتجاه واضح (السعر فوق EMA50 فوق EMA200 أو العكس)، ثم ارتداد السعر إلى EMA50 خلال آخر 6 شموع، ثم شمعة تعود مع الاتجاه وتغلق فوق/تحت الشمعة السابقة. الدخول عند إغلاقها.
- **ارتداد (Reversal):** RSI متطرف (تحت 30 أو فوق 70)، والسعر يكسر أدنى/أعلى 20 شمعة (سحب سيولة)، ثم شمعة رفض (Hammer أو Shooting star أو ابتلاع). هذه صفقة **عكس الاتجاه** فخطرها أعلى، فصغّر حجمها.
- وقف الخسارة خلف آخر قاع/قمة، وبحدود 0.3 إلى 4 ATR، والهدف RR 2.
- لإيقافها: `RCT=0`.

## تنبيهات مبكرة (سحب السيولة وFVG وOrder Block)

بالإضافة إلى إشارة الدخول الكاملة، يرسل البوت تنبيهاً عند ظهور كل عنصر على آخر شمعة مغلقة:

- `SWEEP`: سحب سيولة (اختراق أعلى قمة أو أدنى قاع في آخر 10 شموع ثم إغلاق عائد داخل النطاق).
- `FVG`: فجوة قيمة عادلة بحجم لا يقل عن نصف ATR (لتجنب الفجوات الصغيرة).
- `OB`: Order Block، آخر شمعة معاكسة قبل اندفاع قوي.

هذه تنبيهات **انتباه وليست إشارة دخول**. أقواها أن يأتي سحب سيولة ثم اندفاع ثم FVG، وهذا ما يرسله البوت كإشارة كاملة.

- لتحديد المطلوب: `EVENTS=sweep,fvg` أو `EVENTS=none` لإيقافها (هي كثيرة، خصوصاً على البتكوين).
- **افتتاح الجلسات:** ينبهك البوت عند بدء جلسة لندن وجلسة نيويورك. لإيقافه: `SESSION_ALERTS=0`.

## التأكيدات الكلاسيكية

كل تنبيه ICT يحمل **درجة ثقة من 4** بحسب ما يؤكده من الأمور التالية:

- `trend`: اتجاه المتوسطين EMA50 وEMA200 يوافق الصفقة (شراء مع الصاعد، بيع مع الهابط).
- `fib`: سعر الدخول داخل منطقة فيبوناتشي 0.5–0.786 للموجة من سحب السيولة إلى الاندفاع.
- `level`: الدخول قريب من قمة أو قاع سابق (مستوى دعم أو مقاومة).
- `candle`: شمعة ابتلاع أو شمعة بذيل طويل (Hammer / Shooting star).

مثال: `Confluence: 3/4 (trend, fib, candle)`.

لتصفية الإشارات الضعيفة: `MIN_SCORE=2 python ict_alerts.py` (لا يُرسل تنبيه ICT إلا إذا كانت درجته 2 أو أكثر).

**إشارة كلاسيكية مستقلة** تظهر بعنوان `CLASSIC ALERT`: اتجاه واضح، وارتداد السعر إلى منطقة فيبوناتشي 0.5–0.786 من آخر موجة، وشمعة انعكاس. الدخول عند إغلاق شمعة الانعكاس، ويُوضع أمر Limit عنده. ولإيقافها: `CLASSIC=0`.

المتوسطات تحتاج 200 شمعة على الأقل، وهي موجودة في البيانات المجانية.

## اختبار على بيانات تاريخية

```
python backtest.py BTCUSD 30
```

يحاكي الأوامر على آخر 30 يوماً (للذهب والمؤشرات الحد الأقصى 60 يوماً)، ويطبع عدد الصفقات ونسبة الربح والمحصلة بوحدة R، مع تفصيل حسب درجة الثقة، لترى هل رفع الدرجة يحسّن النتائج فعلاً.

- لا يشمل السبريد ولا العمولة ولا الانزلاق، فالنتائج الحقيقية أسوأ.
- أقل من نحو 30 صفقة لا تدل على شيء.
- الماضي لا يضمن المستقبل.

## ملاحظات

- حجم الصفقة غير محسوب في التنبيه لأنه يختلف حسب الوسيط وحجم العقد، فاحسبه بنفسك من مسافة وقف الخسارة (Risk distance) والمخاطرة التي تناسبك.
- المنطق مطابق للنسخة الأصلية، ومنه أن أمر الـ Limit لا ينتهي ما دام معلقاً، فألغِ يدوياً الأوامر التي لم يعد لها معنى.
- لم أجرّب الاتصال بمصادر الأسعار من بيئة التطوير لأن الإنترنت كان مقيداً فيها، فجرّب أول تشغيل وأخبرني بأي رسالة خطأ تظهر.
- وضع MetaApi (`ict_smc_bot.py`) يحتاج رصيداً في حسابك على MetaApi.

## إصلاحات الأولوية 0 (مراجعة هندسية)
- **لا أوامر حقيقية أبداً:** `ict_smc_bot.py` لا يرسل أي أمر؛ `DRY_RUN` يُتجاهل (تنبيهات فقط).
- **لا إعادة رسم:** قرار الدخول يعتمد على آخر شمعة **مغلقة** فقط، وليس سعر الشمعة الجارية.
- **فحص جودة البيانات** يتجاهل الشمعة الجارية (لكن حداثة البيانات تُفحص عليها).
- **PDH/PWH/PWL** في `structure.py` تدعم تقويم الفوركس/CFD (يوم يبدأ 17:00 نيويورك، الأسبوع من أحد المساء) عبر `calendar="ny17"`؛ الكريبتو `utc`.
- ملاحظة: `structure.py` لم يُربط بعد بالتنبيهات الحية.

## المحرك الانتقائي (الافتراضي الآن) — إشارات فقط
ملف `selective.py`. يعطي إشارة فقط عند اكتمال كل الشروط الإلزامية، وإلا يطبع `WAIT` مع السبب.
- **الأدوار:** H4 وH1 = الاتجاه ومناطق الدعم والمقاومة، M15 = الإعداد (FVG أو Order Block)، M5 = شمعة التأكيد. M1 لا يصدر إشارات.
- **إلزامي:** (1) توافق الاتجاه H4/H1/M15، (6) عودة حقيقية (retest) لمنطقة صالحة، (7) شمعة M5 **مغلقة** (شراء: Close>Open، بيع: Close<Open) تتكوّن عند الـretest أو بعده، (8) سبريد وتكلفة ونسبة ربح/خسارة مقبولة، والبيانات `LIVE`.
- **اختياري ويُحسب في الدرجة /8:** دعم/مقاومة، سحب سيولة، BOS/MSS، FVG/OB. الحد الأدنى الافتراضي 5 من 8 (`MIN_CONFLUENCE`).
- **FVG صالح:** ثلاث شموع حقيقية + شمعة وسطى displacement + سياق هيكلي + غير مستهلكة (fresh أو partial حتى 50%).
- **OB صالح:** أصل displacement كسر الهيكل (BOS/MSS)، لم يُلمس من قبل، لم يُغلق السعر عبره.
- **الدعم/المقاومة:** تجمعات swing مؤكدة (لمستان على الأقل؛ 3 فأكثر = قوية). كسر الذيل (wick) لا يلغي المنطقة، **الإغلاق** خلفها بمقدار 0.25 ATR يلغيها ويقلب دورها.
- **التكلفة:** السبريد مطلوب. اكتبه في الاختصار مثل `SPREAD_XAUUSD=0.30` (بوحدات السعر). بدونه لا إشارة (`WAIT`)، إلا إذا فعّلت `ALLOW_UNKNOWN_COST=1` فتظهر الإشارة موسومة `COST UNVERIFIED`.
- **قفل لكل سوق:** إشارة نشطة واحدة لكل سوق، لا تكرار، لا شراء وبيع معاً. الحالة محفوظة في `signals_state.json` وتبقى بعد إعادة التشغيل. تُحرَّر عند WIN/LOSS/NOT FILLED أو بعد 6 ساعات.
- **حالة البيانات:** LIVE / HISTORICAL / SIMULATED / STALE / DISCONNECTED. غير LIVE = لا إشارة.
- التشغيل: `ENGINE=selective` (الافتراضي) أو `ENGINE=legacy` (المحرك القديم كما كان) أو `ENGINE=both`.
- **اختبار تاريخي:** `backtest_selective.py` (replay شمعة بشمعة مع التكاليف وتقسيم in/out-of-sample). لم يُشغَّل على بيانات حقيقية بعد، فلا توجد نسبة نجاح موثّقة ولا يوجد ادعاء بـ70%.

## وحدة ICT + SMC + السيولة (`ict_engine.py`)
- **السيولة (M15):** BSL/SSL، قمم وقيعان متساوية (EQH/EQL)، أعلى/أدنى اليوم السابق، أعلى/أدنى **الجلسة السابقة** (آسيا 00–07، لندن 07–13، نيويورك 13–21 بتوقيت UTC، بلا تعديل للتوقيت الصيفي)، قمم وقيعان swing مؤكدة، ومستوى لا يُستعمل إلا إذا لم يُلمس.
- **Sweep ≠ Breakout:** sweep = ذيل يخترق المستوى والشمعة **تغلق عائدة** خلفه. إغلاق خلف المستوى = breakout. وSweep يُستهلك إذا أغلقت شمعة لاحقة خلف المستوى أو كُسر طرف الـsweep.
- **تسلسل الانعكاس:** SSL/BSL صالح ← sweep مؤكد ← MSS (CHoCH + displacement) ← FVG و/أو OB صالح من نفس الحركة ← retest ← شمعة M5 مغلقة ← OHLC والسبريد والتكلفة وR:R ← الإشارة. المنطقة يجب أن تكون في discount للشراء وpremium للبيع.
- **الاستمرار:** محاذاة H4/H1، بنية M15، سحب إلى FVG/OB أو دعم/مقاومة، وتأكيد M5 مغلق (القواعد القديمة كما هي). D1 يعارض = WAIT للاستمرار (إن توفرت بياناته).
- **ضد الاتجاه:** يُسمح فقط بتسلسل انعكاس **كامل**، وإلا WAIT.
- **لا إعادة استخدام:** كل حدث سيولة وكل FVG/OB يعطي إشارة واحدة فقط (محفوظ بعد إعادة التشغيل).
- **M1:** للتوقيت فقط ويظهر في التقرير، لا يُنتج ولا يمنع أي إشارة. D1/M1 اختياريان وفشل جلبهما لا يوقف شيئاً.
- **التقرير:** كل إشارة وكل WAIT يشرح: اتجاه HTF، السيولة المسحوبة، BOS/MSS، صلاحية FVG/OB والـretest، شمعة M5 (OHLC)، الدخول والإبطال وSL وTP، وأسباب الانتظار. للـWAIT الكامل شغّل بـ `VERBOSE=1`.
- لا توجد نسبة نجاح موثّقة. 70% لا يُدّعى قبل اختبار خارج العينة وتجريبي أمامي (forward) بعينة كافية.

## تنبيهات الدخول بالعربية
تنبيه الدخول من المحرك الانتقائي يصدر الآن بالإنجليزية افتراضياً (`ALERT_LANG=en`). للعربية: `ALERT_LANG=ar`. المصطلحات الفنية (FVG, OB, MSS, SSL) تبقى بالإنجليزية.

## الصوت
الصوت (والإشعار العالي) فقط عند **صفقة جاهزة للدخول**. الجلسات والأخبار وافتتاح السوق الأمريكي ونتائج الصفقات وتنبيهات التحذير المبكر (sweep/FVG/OB) تظهر بإشعار هادئ بلا صوت. في Telegram تصل الرسائل غير الدخول بدون صوت.

## ألوان تنبيه الدخول
**BUY أزرق، SELL أحمر:** دائرة ملونة 🔵/🔴 في عنوان الإشعار ونصه وفي Telegram، ولون الشاشة (أزرق/أحمر) في Termux، ولون ضوء الإشعار (LED) إن كان جهازك يدعمه. الإشعارات الأخرى بلا لون.

## صمت كامل إلا عند صفقة
الصوت والإشعار على الهاتف **فقط** لإشارة دخول جاهزة. كل شيء آخر (جلسات، أخبار، افتتاح السوق الأمريكي، نتائج الصفقات، إنذارات sweep/FVG/OB) يُطبع على شاشة Termux فقط بلا إشعار ولا صوت. لإظهارها كإشعار هادئ بلا صوت: `INFO_NOTIFY=1`.

## الفحص الشامل والإصلاحات (test_audit.py)
**ما فُحص:** 120 حالة بيانات عشوائية (تقلب عالٍ/منخفض، شموع مسطحة، مدى صفري) بلا أي انهيار؛ إضافة شموع مستقبلية لا تغيّر القرار (لا look-ahead)؛ الشموع المكررة؛ السبريد المجهول؛ إعادة التشغيل.
**ما أُصلح:**
- **كاش للشموع (45 ث):** كان المحركان يحمّلان البيانات نفسها مرتين، مما قد يسبب حظراً من Yahoo.
- **التايم فريمات الافتراضية** مع المحرك الانتقائي: `5m,15m,1h,4h` بدل ست فريمات (لا 1m ولا 1d).
- **فحص جودة البيانات** للمحرك الانتقائي (ترتيب، تكرار، OHLC مستحيل، قفزة شاذة) قبل أي قرار.
- **حجب الأخبار عالية التأثير** (30 دقيقة قبل و15 بعد) افتراضياً مع المحرك الانتقائي: `NEWS_BLOCK=0` للإيقاف. مصدر الأخبار غير مؤكد من هاتفك: إن ظهر `news calendar unavailable` فالحجب غير فعّال.
- **حد المخاطرة اليومي:** بعد 3 خسائر متتالية (أو خسارة 1.5% من الحساب في اليوم، أي 6R عند مخاطرة 0.25% للصفقة) لا إشارات جديدة حتى اليوم التالي بتوقيت UTC. يعمل مع التتبع (`TRACK=1`). الإعدادات: `MAX_DAILY_LOSSES` و`MAX_DAILY_LOSS_PCT` و`RISK_PERCENT` (الافتراضي 0.25 للتجربة الورقية) و`MAX_DAILY_LOSS_R` (يلغي الحساب التلقائي، و0 للإيقاف). التكاليف غير مضمّنة في هذا الحساب.
- **حجم اللوت** يظهر في تنبيه الدخول عند ضبط `BALANCE` (مع `CONTRACT_<SYMBOL>` أو مواصفات موثّقة).
- **إعادة تشغيل تلقائية** بعد 30 ثانية عند أي انهيار غير متوقع، ونبضة "bot is running" كل ساعة.
- شموع مكررة تُحسب مرة واحدة.
**ما لا يزال غير مثبت:** أداء الاستراتيجية (لا backtest حقيقي)، مطابقة أسعار Yahoo/Binance لأسعار JustMarkets، مصدر الأخبار، والسبريد الفعلي. في اختبار البيانات العشوائية خرجت إشارات بنسبة نحو 1% بلا أي ميزة حقيقية، أي أن غياب الإشارات لا يثبت جودتها ولا وجودها يثبتها؛ الحكم للـbacktest والتجريب الأمامي فقط.

## ملف "نشط" لإشارات أكثر (`PROFILE=active`)
الافتراضي `strict` (متشدد). `PROFILE=active` يعطي إشارات أكثر: حد التوافق 4 من 8 بدل 5، نافذة retest أطول (5 شموع M5)، مناطق أقدم، وFVG حتى 70% مملوء. **لا يُخفَّف أي شرط إلزامي**: الشمعة المغلقة، محاذاة الاتجاه أو تسلسل انعكاس كامل، الـretest، التكلفة، الأقفال، حد الخسارة اليومي. الإشارات بهذا الملف تحمل وسم ACTIVE. إشارات أكثر تعني جودة متوسطة أقل غالباً، ولا توجد نتائج مقاسة تفضّل أحد الملفين: قارنهما بالتتبع.
في سطر الانتظار يظهر `[setup forming: reversal stage N/4]` عندما يقترب الإعداد من الاكتمال.

## أخطاء مصدر البيانات (HTTP 404 على الذهب)
`XAUUSD=X` قد يرجع 404 من Yahoo مؤقتاً. صار البوت يجرّب **مضيفين** (query1 ثم query2)، ثم **بديلاً** للذهب `GC=F` (عقود آجلة) لمدة 30 دقيقة لكل الفريمات معاً (لا خلط بين رمزين). عند استخدام البديل يطبع `DATA SOURCE:` وتحمل الإشارة سطر تحذير: سعر العقود يختلف عن الفوري بفارق، فقارن بـMT5 واضبط `OFFSET_XAUUSD`. لفحص المصادر الآن: `python check_feeds.py`.

**تحديث (الذهب):** إن فشل `XAUUSD=X` يُستخدم أولاً `PAXGUSDT` من Binance (رمز مدعوم بالذهب ويُتداول لحظياً 24 ساعة؛ سعره قريب من الفوري لكنه ليس فورياً، ويتغيّر الفارق)، ثم `GC=F` (عقود آجلة، وبيانات Yahoo لها متأخرة نحو 5–10 دقائق، فقد لا تصلح لشمعة M5). لتغيير الترتيب: `FALLBACK_XAUUSD=GC=F,binance:PAXGUSDT`، ولمنع البدائل: `FALLBACK_XAUUSD=none`.

## الصفقات المفتوحة
`python open_trades.py` يعرض الإشارات التي يتابعها البوت الآن (لم تصل TP أو SL) ونتائج اليوم. هذه إشارات البوت، وليست صفقاتك الحقيقية في MT5 (البوت لا يرى حسابك): صفقاتك الفعلية في MT5 تبويب Trade. النتائج المنتهية في `python stats.py`.

- **التقييم من 100 (Score):** HTF 20، السيولة 15، MSS/BOS 15، Displacement 15، FVG/OB 15، تأكيد M5 10، التنفيذ وRR 10. أكثر من 85 مؤهل للإشارة بشرط تحقق الشروط الإلزامية، من 75 إلى 85 WATCH (لا إشارة)، وأقل من 75 REJECT. الدرجة ترتيب لجودة الإعداد وليست احتمال ربح. الحد قابل للتعديل بـ `READY_SCORE` لأغراض الاختبار فقط. ومن دون سحب سيولة لا يمكن عملياً الوصول إلى 86.
- **الحالات النهائية للإشارة:** TP_HIT وSL_HIT وCANCELLED وEXPIRED (تُسجَّل في `signals_state.jsonl` وفي `results` داخل ملف الحالة؛ ملف `results.csv` يبقى بالأسماء win/loss).

## الاختبار التاريخي (Backtest) على بيانات حقيقية

الملف `backtest_runner.py` يقرأ شموع M5 من ملف CSV (تصدير MT5 أو أي ملف بأعمدة time,open,high,low,close) ويبني منها M15 وH1 وH4 وD1، ثم يعيد تشغيل المحرك شمعة بشمعة بدون نظر إلى المستقبل، مع الأقفال وعدم إعادة استخدام المناطق والحماية اليومية والتكاليف، وأي شمعة تلمس الوقف والهدف معاً تُحسب خسارة.

```
python backtest_runner.py --market XAUUSD --m5 XAUUSD_M5.csv --spread 0.26 --tz-offset 3 --out report.json
```

- `--tz-offset`: كم ساعة يسبق توقيت ملف الوسيط توقيت UTC (غالباً 2 أو 3). خطؤه يفسد الجلسات.
- `--spread`: بوحدة السعر. بدونه لا تصدر أي إشارة (التكلفة مجهولة).
- التقرير: النتائج لكل سوق، استمرار مقابل انعكاس، حسب الدرجة، in-sample مقابل out-of-sample، ونوافذ زمنية متتالية للاستقرار، وأهم أسباب منع الإشارات.
- أقل من 200 صفقة لكل سوق يعني أن الأرقام لا تثبت شيئاً، والتقرير يقول ذلك. والنتيجة التاريخية لا تعد بنتيجة مستقبلية.
- لا توجد بيانات داخل المستودع، والاختبارات تستعمل شموعاً عشوائية مصطنعة للتحقق من الكود فقط.
- **تنزيل بيانات البتكوين المجانية:** `python fetch_binance_history.py --symbol BTCUSDT --days 365 --out BTCUSDT_M5.csv` (يعمل على الهاتف في Termux، ويكمل من حيث توقف). أسعار Binance تختلف قليلاً عن وسيطك.
- **الملف active (محدَّث):** يقبل الآن درجة 80 فأكثر (strict يطلب 86). الإشارة بدرجة 80-85 تحمل وسم REDUCED CONFIDENCE / «ثقة أقل». قواعد الأمان (شمعة M5 مغلقة، التكاليف، حالة البيانات، الأقفال، الحماية اليومية 3 خسائر / 1.5%) كما هي في الملفين. أنفع ما يمكن قياسه لاحقاً بالـ Backtest: هل صفقات 80-85 أسوأ فعلاً من 86+؟ (التقرير يفصلها حسب الدرجة).
- **بيانات الذهب (spot) من Dukascopy:** `python fetch_dukascopy_history.py --symbol XAUUSD --days 90 --out XAUUSD_M5.csv` (للـ Backtest فقط، تاريخية وليست حية). تنسيق الملفات مأخوذ من سكربتات عامة وليس من توثيق رسمي، فقارن أول الأسعار المطبوعة بسعر حقيقي قبل الاعتماد عليها. رموز المؤشرات والبتكوين ومقاييس أسعارها غير مؤكدة.

## الملف `loose` (أقل فلتر) وتصحيح مهم
- `PROFILE=loose` يقبل إشارات درجتها **60 فأكثر**، ويعدّ شمعة M5 المغلقة «قوية» أيضاً إذا كان جسمها 60% أو أكثر من مداها، ويحمل وسم LOOSE. حد التوافق يبقى 4 من 8 لأن 60% من 8 هو 5 وهو ما يطلبه strict أصلاً. **لا تتغير الشروط الإلزامية**: شمعة M5 مغلقة، التكلفة، حالة البيانات، الأقفال، الحماية اليومية، واتجاه الفريمات أو تسلسل انعكاس كامل. الجودة المتوسطة أقل بوضوح ولا توجد نتيجة مقاسة تثبت أنه يربح.
- **تصحيح:** كان البوت الحي (`ict_alerts.py`) يفرض حد الدرجة 86 حتى مع `PROFILE=active` (متغير READY_SCORE غير المضبوط كان يلغي قيمة الملف). صار كل ملف يحتفظ بقيمه، ولا يُغيَّر إلا ما تضبطه بمتغير. لذلك الملف active لم يكن 80 فعلاً في البث الحي قبل هذا الإصلاح.

## مراجعة الإشارة بالذكاء الاصطناعي قبل الدخول
- عند كل **إشارة دخول** ينسخ البوت إلى حافظة هاتفك نصاً جاهزاً: طلب مراجعة + الإشارة كاملة (يحتاج تطبيق Termux:API)، ويحفظه أيضاً في `last_signal_review.txt`. الصقه في محادثة مع المساعد ليحلّله كرأي ثانٍ: مدى توافقه مع القواعد، نقاط الضعف والمخاطر، وما تتحقق منه في MT5.
- هذا رأي مراجع وليس تنبؤاً بالسعر ولا ضماناً. `REVIEW_COPY=0` يلغيه. لا يتصل البوت بأي خدمة ذكاء اصطناعي (لا تكلفة ولا مفاتيح).


## Several signals per market
`MAX_ACTIVE` (default 3) = open signals allowed on one market at once; `ALLOW_OPPOSITE=0` stops a BUY and a SELL being open together. `MAX_ACTIVE=1` restores the old one-signal lock. The daily risk guard still counts every loss, so stacking cannot bypass it.

## Scalp profile
`PROFILE=scalp`: quick trades. Target 1:1.2 (strict is 1:2), stop at most 2 M15 ATR, score gate 73, net R:R after costs 0.9+. Entry rules are active's. Direction comes from M5 + M1 structure (`ltf_bias`) instead of H4 + H1: both must agree with the trade (if M1 candles are unavailable M5 alone decides). M15 no longer has to agree (it still supplies the zones and the sweep). Only a trade with BOTH H4 and H1 against it is still blocked, and D1 no longer blocks. Not measured yet: backtest it before trusting it (`python backtest_runner.py ... --profile scalp`).

## Coloured management alerts
Green `TRADE +1R` = in profit, move the stop to the entry. Yellow `REVERSAL WARNING` = profit is fading, consider moving the stop to the entry. Red band `CLOSE NOW` = profit (at least +0.5R earlier) is gone and price is back at the entry: close by hand. `RESULT: WIN` is green, `RESULT: LOSS` red. Entries stay blue (BUY) / red (SELL). Messages only, never orders; `MANAGE_SOUND=0` mutes the tone.

## US index hours
US30 and US100 use free Yahoo feeds that are only live in the US cash session (09:30-16:00 New York, Mon-Fri). Outside it the bot prints `MARKET CLOSED (... opens in Xh Ym)` and does not scan or fetch. `US_HOURS_ONLY=0` turns this off. Holidays are not listed (the stale-data check still blocks them).

## Analyse now
`python analyze_now.py XAUUSD` runs the full selective analysis once and prints the report (nothing saved, no sound). Works with `PROFILE=scalp` etc.

`python analyze_now.py` also prints a `Direction:` line (H4 to M1 structure) and says if M5 and M1 agree.

`python analyze_now.py` also prints the Fibonacci of the latest M15 leg: 50 / 61.8 / 70.5 / 78.6% retracements, the 62-79% OTE zone, 1.0 / 1.272 / 1.618 extension targets and whether price is inside the OTE. Informational only: it does not create or block signals.

## Sweep, counter-trend, timeframes
`PROFILE=scalp` now requires a valid liquidity sweep before an entry (`REQUIRE_SWEEP=0/1` overrides it for any profile). A reversal entry (sweep, MSS, FVG/OB, retest) against H4/H1 is labelled COUNTER-TREND in the alert. Every alert has a `Timeframes` line with the structure of M1, M5, M15, H1 and H4 against the trade (check = agrees, cross = against, ? = unknown). Information only: M1 never blocks.

`python calc_offset.py XAUUSD <MT5 Bid now>` prints the OFFSET_XAUUSD value (broker price minus the feed price).

## Hourly "why no signal" summary
Every hour the bot prints `LAST HOUR: N signal(s)` and, per market, the top 3 reasons its scans waited (for example `7x scalp bias: M# down, M# up ...`). Use it to see which rule blocks the signals before changing anything.

## Swing profile
`PROFILE=swing`: slower, bigger trades with the full rule set (H4/H1 alignment or a complete reversal, D1 context, sweep, FVG/OB, retest, M5 confirmation). Target 1:3, stop up to 4 M15 ATR, score gate 80, 5 confluences, zones up to 4 days old. A signal stays open 72 h (`SIGNAL_TTL_HOURS` overrides). Fewer signals; not measured yet.

## Several profiles at once
`PROFILE=all` (or a list such as `PROFILE=scalp,swing`) checks every listed profile on each scan; the first one whose rules pass gives the signal (strict, swing, active, scalp in that order, so the most selective label wins). The same setup is never sent twice (shared signal ids and locks). More signals than any single profile, and each keeps its own rules; the daily risk guard still counts every loss. `python analyze_now.py` accepts the same list.

## Fibonacci golden zone
`PROFILE=golden` = active entry rules, but a signal is only given when the entry sits in the 62-79% golden zone of the latest M15 leg (an up leg for a buy, a down leg for a sell), target 1:2. `REQUIRE_FIB=1` adds the same rule to any profile. Every signal shows a `Fib:` line with the retracement of the entry. `PROFILE=all` includes golden (after strict and swing).

`ALLOW_UNKNOWN_COST` now defaults to on: when a market has no verified spread the signal is still given and labelled `COST UNVERIFIED`. `ALLOW_UNKNOWN_COST=0` makes the cost check block it instead. Fill the real spread in markets.json (or SPREAD_<SYMBOL>) to remove the label.

When the bot starts (and again at every US open and close) it prints the state of US30 / US100: `MARKET IS OPEN now`, `market closed, opens in ...` (with the opening time in your phone's own timezone). At the open it also shows a green `US MARKET OPEN` notification with the tone. `US_HOURS_ONLY=0` turns this and the closed-market skip off.

## Forex profile (`PROFILE=forex`, EURUSD / GBPJPY / JPYGBP)
- Direction and bias: H4 + H1. The signal is created by the **closed M15 candle** (primary confirmation); the closed M5 candle only adds precision points and can never trigger a signal alone. The M15 candle must have closed within the last 7 minutes.
- Score 0-100: H4/H1 alignment 15, liquidity sweep 15, BOS/CHoCH 15, FVG 10, order block 10, retest 15, M15 confirmation 10, M5 confirmation 5, S/R alignment 5. Below 70: no signal. 70-79 moderate, 80-89 strong, 90+ very strong. It ranks setup quality; it is NOT a win probability.
- Two targets: TP1 at 1:1.5 (take partial, move the stop to entry) and TP2 at 1:3. Results in `results.csv` are judged on TP2/stop.
- Shown for every signal: stop distance in pips, the stop in M15 ATR, the cost assumed (spread unverified until `markets.json` is filled).
- No signal during the weekend, the 21:00-24:00 UTC rollover, or when the M15 ATR is more than 2.5x its recent median. One open signal per symbol. Duplicate setups are blocked by the shared signal ids.
- The forex profile is only applied to forex pairs; with `PROFILE=all` forex pairs use it and every other market keeps the other profiles. Run alone with `PROFILE=forex SYMBOLS=EURUSD,GBPJPY`.
- Not yet verified: the broker symbol names/suffixes, spreads and specs (they must be copied from MT5 into `markets.json`), and the performance (no backtest has been run on forex data).

## Gold price link and US feeds
`python calc_offset.py XAUUSD <MT5 Bid now>` saves the broker-minus-feed difference in offsets.json; the running bot picks it up by itself and analyze_now uses it (OFFSET_<SYMBOL> still overrides). `--show` lists it, a 0 value removes it.
US100 / US30 now use the E-mini futures (NQ=F, YM=F, about 23 h a day) so they work outside the 09:30-16:00 New York cash session; `US_FEED=cash` returns to ^NDX / ^DJI (then "market closed" outside that session). The futures price differs from your CFD by a small basis: set it with calc_offset.py.

`FEED_<SYMBOL>=binance:PAXGUSDT` (or `yahoo:GC=F`) makes that feed the first choice for a market, for example `FEED_XAUUSD=binance:PAXGUSDT` takes gold from Binance and never asks Yahoo. PAXG is a gold-backed token: its price follows spot gold closely but not exactly, so set the offset with calc_offset.py.

## Regular market report
At start and then every `REPORT_MINUTES` (default 60, 0 = off) the bot prints one organised block: a line per market with the price (offset applied), the structure direction of H4, H1, M15, M5 and M1, whether M5 and M1 agree (scalp bias) and the main reason it is waiting. Together with the hourly `LAST HOUR` summary and the entry alerts this gives a regular, readable analysis.

## Lot size, specifications and the dollar
- Lot size: set `BALANCE=<your balance>` in the shortcut; each alert then shows the lot (rounded down to the broker step). It needs the verified MT5 specification of the symbol: fill it with `python set_spec.py XAUUSD tick_size=0.01 tick_value=1 spread=0.25 commission_per_lot=0 product_type=spot_cfd` (values from MT5 > symbol > Specification, nothing is guessed; the command prints what is still missing). `python markets.py` shows every market.
- Dollar monitor: the US dollar index (Yahoo DX-Y.NYB) is shown at the top of every market report and, for XAUUSD / EURUSD / BTCUSD, as a context line in each signal saying whether the dollar trend supports or opposes the trade. Context only, it never blocks a signal. `USD_MONITOR=0` turns the signal line off.

## Tightened filtering (default on)
- Phone alert only at score **82+** (`MIN_NOTIFY_SCORE`); 75–81 prints `WATCH` in the console only.
- COUNTER-TREND needs score **90+** and a strong confirmation candle (`COUNTER_TREND_MIN_SCORE`, 0 = off).
- Entry needs an MSS/CHoCH with displacement (≥1 M15 ATR) **and** a valid FVG (`REQUIRE_MSS_FVG=0` to relax).
- Net R:R ≥ **1.5** after the spread × 1.25 (`MIN_NET_RR`, `SPREAD_MARGIN`); scalp target raised to 1:2.
- Killzones UTC: London 07–11, New York 12–16 (`KILLZONE_LONDON=7-11`, `KILLZONE_NY=12-16`, `KILLZONES=0` = off).

## Two modes: SCALP and SWING (`PROFILE=scalp,swing`)
| | SCALP | SWING |
|---|---|---|
| Trend (must agree) | H1 + M15 | Daily + H4 |
| Zone of interest | M15 FVG/OB, retest on M5 | H1 FVG/OB (sweep and MSS read on H1) |
| Entry confirmation | closed M5 + last closed M1 and M3 | closed M15 + closed M5 agrees |
| Time (UTC) | London 08:00-11:00, New York 13:00-16:30; muted otherwise | all day except 20:00-22:00 (not crypto) |
| Score / net R:R | 85+ / 1.5+ after spread x1.25 (target 1:2, TP1 1:1) | 80+ / 3.0+ after spread x1.25 (target 1:3.5, TP1 1:1.75) |
Both: never against their own trend; strict sequence sweep -> MSS (displacement >= 1.5 ATR) -> retest of a FRESH FVG/OB;
signal cancelled if price already went >50% of the way to TP1 before coming back; at +1R an alert "MOVE SL TO ENTRY".
`KILLZONES=0` switches the time filters off. Signals only; no measured win rate.

### More signals (optional, each lowers quality)
SWING now also accepts a fresh H4 FVG/OB as the zone of interest after an H1 sweep + MSS (on by default).
Relax on purpose with env variables: `MIN_DISPLACEMENT=1.2` (default 1.5), `LATE_ENTRY_FRAC=0` (never cancel late entries),
`MIN_NET_RR`, `KILLZONES=0`, `KILLZONE_LONDON=7-12`, `KILLZONE_NY=12-17`.

## Strategy per pair (selector)
Every pair only runs the strategies that suit it (`PAIR_STRATEGIES` in ict_alerts.py): gold scalp/swing/golden/active/strict;
BTC and indices scalp/swing/active/strict; EURUSD/GBPJPY/JPYGBP forex/swing/scalp. All allowed strategies are evaluated;
the best scoring one that passes gives the signal and the alert shows how many agree. `PAIR_STRATEGIES=0` = old behaviour.
This is a rule-based selector, not a trained model; no profit is promised.

## Seven markets
XAUUSD, NAS100 (US100), US30, BTCUSD, EURUSD, GBPJPY and **DAX40** (opt-in: `SYMBOLS=BTCUSD,XAUUSD,US100,US30,EURUSD,GBPJPY,DAX40`).
DAX40 uses the free Yahoo `^GDAXI` cash index, so it only scans during Xetra hours 09:00-17:30 Frankfurt (`DAX_HOURS_ONLY=0` to disable).
Your broker's name (GER40 / DE40 / DAX40.m ...) is never assumed: copy it from MT5 > Market Watch > Specification and run
`python set_spec.py DAX40 ...` like the other markets. Test one market first: `python analyze_now.py DAX40`.

## KENAN TRADER AI signal layout, VWAP, ADX, TP3
- Alerts use the KENAN TRADER AI layout (MARKET, DIRECTION, SIGNAL STATUS, ENTRY, STOP LOSS, TP1/TP2/TP3 ... INVALIDATION CONDITION).
  `SIGNAL_FORMAT=classic` brings back the older text.
- TP1 = half of TP2 (move the stop to entry), TP2 = the profile target, TP3 = 1.5 x TP2 distance (runner).
- Session VWAP (anchored per market: crypto 00:00 UTC, US indices 09:30 New York, DAX 09:00 Berlin, FX/gold 17:00 New York) and
  ADX(14) on M15 are context only: up to +3 points each (+6 max, capped at 100), never an entry on their own and they never
  replace a mandatory ICT condition. Without volume in the feed (most Yahoo FX/index data) VWAP uses equal weights and says so.
- COUNTER-TREND needs a complete reversal (sweep, CHoCH/MSS, displacement, FVG, retest), a closed confirmation candle and
  score 70+ (`COUNTER_TREND_MIN_SCORE`); a phone alert still needs the profile score gate (82+ by default), so in practice the higher of the two applies.

## Zone-reached explanation
When price returns into a FVG / order block created by a complete sweep + MSS but the entry candle has not closed yet, the bot
prints (once per zone, no sound) a short plain explanation: what happened in order, what it waits for, where the setup is
cancelled, and `WAITING FOR CANDLE CONFIRMATION - do not enter yet`. Arabic with `ALERT_LANG=ar`. `ZONE_WATCH=0` turns it off.

## Paper bot (simulation only): `paper_bot.py`
A separate, simple bot: EMA 9/21 cross + RSI 50 on BTCUSDT 15m (public Binance candles, no API key). It keeps a virtual
balance of 1000 with SL 1.5%, TP 3%, 1% risk per trade, slippage 0.05% and fee 0.1% per side. It cannot place real orders.
Install: `pip install requests python-dotenv`, copy `.env.example` to `.env` (optional), run `python paper_bot.py`
(`--once` checks one time). Log: `trading_bot.log`; trades: `paper_trades.csv`; state: `paper_state.json`.

## Volume confirmation of the displacement candle (default on)
A displacement only counts when, at its close, ALL hold: volume >= 1.5 x the average volume of the previous 20 candles (the
current one excluded), body > 1.2 x the average body of those 20, and the candle broke the structure (MSS/CHoCH) and left a FVG.
Multipliers live at the top of `indicators.py` (`VOLUME_MULT`, `BODY_MULT`, `VOLUME_LOOKBACK`) or per run with `VOLUME_MULT=`,
`BODY_MULT=`; `VOLUME_FILTER=0` turns it off. Where the feed has no volume (many Yahoo forex/index series) the volume rule is
skipped and the alert says `Volume: n/a`; `VOLUME_REQUIRED=1` rejects such signals instead. Alerts show `Volume Spike: 1.8x Avg`.

## More signals: `SIGNAL_LEVEL`
The bot scans every minute (new candles are picked up as soon as they close). How many signals you accept is one switch:
`SIGNAL_LEVEL=strict` (default, approved rules), `balanced` (displacement 1.2 ATR, volume 1.3x / body 1.1x, late-entry 60%,
trading hours 07-17 UTC, alert score 82/78/80 for scalp/swing/other) or `active` (displacement 1.0, volume 1.0x, no late-entry
cancel, no time filter, alert score 80/76/78). Never relaxed: net R:R floor (1.5 / 3.0), the sweep-MSS-FVG logic, news filter,
duplicate protection. Any explicit setting (`MIN_DISPLACEMENT=`, `READY_SCORE=` ...) beats the preset. More signals means lower quality.

## Audit follow-up
- Alert score gate is now **80** (`MIN_NOTIFY_SCORE`; scalp keeps 85). Scores below show as WATCH on the screen.
- A closed **M15 candle must confirm** every signal (`M15_CONFIRM=0` off; `SIGNAL_LEVEL=active` turns it off).
- News data status: alerts say `NEWS DATA MISSING` / `STALE` instead of "clear" when the calendar is unknown (retry every 10 min after a
  failure). `NEWS_STRICT=1` blocks all signals while the calendar is missing or stale.
- Zones already explained are remembered in `zone_seen.json` (24 h) so a restart does not repeat them.
- `ict_smc_bot.py` (legacy MetaApi bot) never sends orders; its docstring now says so.

- News block window is now 15 min before to 30 min after a high-impact event (NEWS_BEFORE_MIN / NEWS_AFTER_MIN change it); alerts show SIGNAL EXPIRY.

## Six alert classes (watch alerts, never entry signals)

While a market is on WAIT the bot tracks each sweep/reversal setup and fires, once per setup, one of: ZONE APPROACH (price within 0.5 ATR of the FVG/OB), ZONE ENTRY (price inside it), REVERSAL STAGE 3 (MSS/CHoCH with displacement after a sweep), RETEST (price returns to the zone), SETUP INVALIDATED (close beyond the sweep extreme or through the zone), and the CONFIRMED BUY/SELL signal itself. Each class has its own vibration pattern and spoken label. Only the CONFIRMED signal is a trade signal; the others say "NOT an entry signal".

State is saved in `alerts_state.json` (survives restarts, forgets setups after 24 h, so no duplicates). Switches: `ALERT_CLASSES=0` (old single zone explanation), `ALERT_VOICE=0` (no speech), `ALERTS_STATE_FILE=<path>`.

## Reversal states and trade journal

Every tracked setup shows one state: REVERSAL DETECTED, REVERSAL DEVELOPING, MSS CONFIRMED, RETEST PENDING, ENTRY CONFIRMED, INVALIDATED (print-only; only ENTRY CONFIRMED is a signal). Finished trades are also written to `journal.csv` (next to `results.csv`, which keeps its old format) with MFE / MAE in R. `python journal.py` prints per-market trades, win rate, avg R, profit factor, max drawdown, average MFE/MAE and false-signal rate (a loss that never reached +0.5R). Fewer than 30 trades means no conclusion.

## Defaults changed: alert score 70, H1 as the general frame

Phone alerts now start at score 70 (`MIN_NOTIFY_SCORE=70`; scalp keeps 85, swing keeps 80; raise it back with `MIN_NOTIFY_SCORE=80`). The general market frame is H1 + M15 trend (`HTF_MODE=h1m15`, set `HTF_MODE=default` for the old H4/H1/M15 rules; swing keeps D1 + H4). A lower score means more but weaker signals: it is a technical rating, not a win probability.

## TP4 / TP5 (display only)

The KENAN signal now also lists TP4 and TP5 (2.0x and 2.5x the TP2 distance) as extended runner levels. The tracker and the results still use TP2 for win/loss; TP4/TP5 are only for you to trail the trade by hand. The bot's order blocks and FVGs are its supply/demand zones.

## New York session engine (phase 1, display only)

`ny_session.py` reads closed M15 candles in America/New_York time (daylight saving handled by the timezone, not a fixed UTC hour). It lists the Asia, London, previous-day and New York opening-range levels and classifies eleven opening scenarios: buy-/sell-side sweep reversals, London High/Low and Previous Day High/Low sweeps, Opening Range Breakout, Failed Breakout, Judas Swing, trend continuation and New York reversal. A sweep alone is shown as UNCONFIRMED; it only becomes a reversal after a close-based structure break with displacement. The result appears at the end of the scan line and in the signal's SESSION line. It does NOT change any signal decision yet. `NY_SESSION=0` turns it off. Not yet measured on real results.

## Market state, IFVG and mitigation blocks (phase 2, display only)

`market_state.py` names the stage the market is in on M15: REVERSAL, LIQUIDITY SWEEP, DISPLACEMENT, RETEST, TRENDING, ACCUMULATION, DISTRIBUTION, RANGING or WAITING FOR CONFIRMATION (strongest evidence first; ADX 25 / 20 and a 20% volatility contraction are plain constants, not tuned on results). It shows as `[state: ...]` on the scan line and `MARKET STATE:` in a signal; `MARKET_STATE=0` turns it off. It never creates or blocks a signal. `structure.py` gained `ifvgs()` (a fair value gap price closed through, now working the other way) and `mitigation_blocks()` (the last opposite candle of a failed swing, after a displaced break); they are tested helpers, not yet part of the entry decision. OTE (0.62-0.79) and the Fibonacci levels 0.5 / 0.618 / 0.705 / 0.786 already existed in `structure.premium_discount`.

## RSI and MACD in the signal (display only)

The KENAN signal now shows `RSI:` (Wilder, 14) and `MACD:` (12/26/9 histogram) on M15 with whether each agrees with the direction, and warns when RSI is stretched against the trade (BUY at 70+, SELL at 30-). They are information for you: they do not create, block or score a signal.

## Performance report by session, market and strategy

`journal.csv` now also stores the entry time, the New York based session (Asia, London, London / New York pre-open, New York open, New York, After hours), the profile (scalp, swing, forex...) and the mode (reversal / continuation). An older journal is upgraded automatically (old rows keep empty new columns, shown as `unknown`). Run `python journal.py` (symbol, session and profile) or pick: `python journal.py session`, `python journal.py profile mode side`. The session view adds a combined "New York (all)" line. Every line shows trades, win rate, total and average R, profit factor, max drawdown, MFE/MAE and false signals; with fewer than 30 trades it says the sample is too small. These are results of your own signals, not a forecast.

## Reversal state machine, one direction at a time, rejection log

`reversal_machine.py` keeps one setup per market and direction, with the stages in order and never skipped: SWEEP DETECTED, MSS CONFIRMED, DISPLACEMENT CONFIRMED, WAITING FOR FVG/OB RETEST, WAITING FOR CANDLE CONFIRMATION, FINAL BUY / FINAL SELL (only the normal signal path issues FINAL: every gate, the closed M5 confirmation candle, and a score of 70+, which is a technical rating and not a win probability). A sweep, an MSS or a CHoCH alone is never an entry; a CHoCH without displacement does not advance. One direction leads per market: the other is shown once as SUPPRESSED with the reason (it conflicts with the leader, or a signal is open) and returns when the leader ends. A setup ends as INVALIDATED (structure level broken), EXPIRED (`WAIT_MSS_BARS`, default 12 M15 candles without an MSS; `SETUP_MAX_BARS`, default 32) or SUPERSEDED (a newer or more advanced setup of the same side). The signal shows `ANALYSIS STAGES:` and `HTF NOTE:` (how H4 and H1 relate to the direction, with the reason when one is against).

Defaults changed: `MAX_ACTIVE=1` and `ALLOW_OPPOSITE=0` (one open signal per market, no BUY and SELL together). Set `MAX_ACTIVE=3 ALLOW_OPPOSITE=1` for the old behaviour.

Every rejection is logged to `rejections.log` (one JSON line per change of reason, per market and side, capped at 512 KB). Read it with `python why.py XAUUSD` (last 10) or `python why.py XAUUSD 30`. The older `reversal_tracker.py` is kept but no longer used by the scan.

## State flip and IFVG watch (analysis alerts, all markets)

`flip_ifvg.py` turns a failed FVG into a reversal setup, without chasing. A **STATE FLIP** needs a full-body M15 close beyond the far edge of the FVG with displacement, and the next closed candle must stay outside (a wick through the edge, or an immediate close back inside, keeps the old scenario alive). The zone then becomes an IFVG and a setup in the other direction starts; the old setup on that FVG is finished. A cooldown per market (`FLIP_COOLDOWN_MIN`, default 60) stops the state flipping back and forth. Then: **IFVG RETEST** when price returns into the zone (never chase: more than 1 ATR away without a retest drops the setup); **IFVG CONFIRMED** when a closed M5 candle closes inside the zone in the trade direction and the next liquidity is at least 1R away, with a suggested entry (50% of the zone), stop (beyond the invalidation plus 0.25 ATR) and targets as analysis. A full M15 body close back through the IFVG invalidates it. Alerts use the layouts of the method (setup / confirmed / state flip) with timeframe, H1 trend, session, reason, invalidation, next liquidity and a 4-condition confluence count (H1 bias, sweep first, strong session, true body break), in Arabic with `ALERT_LANG=ar`. They are analysis and watch alerts only: the bot's real signal is still the FINAL stage of the state machine. `FLIP_IFVG=0` turns it off. Not measured on real results.

## IFVG as a real signal, and your ENTER / WAIT / IGNORE decision

**IFVG signal (`IFVG_SIGNALS=0` turns it off).** A confirmed IFVG (state flip, retest, closed M5 candle inside the zone, 1R of room to the next liquidity) is issued as a real signal only if ALL of these hold: a transparent 100-point score of at least `IFVG_MIN_SCORE` (default 70; 80 when the H1 trend is against it): H1 bias 25 (10 if neutral), sweep first 20, strong session 15, displacement 15, retest and M5 confirmation 15, room to target 10 (5 if 1-2R); gross R:R of at least `IFVG_MIN_RR` (default 2) to the final target; net R:R and cost within the usual limits (spread, commission, slippage); the confirmation candle closed no more than `IFVG_MAX_AGE` seconds ago (default 420); and the market has no other open signal and none in the opposite direction. The score is a technical rating, not a win probability. Otherwise it stays an analysis alert that says why it was not issued (also in `rejections.log`). IFVG signals are tracked like the others with profile `ifvg` and mode `flip`, so `python journal.py profile` measures them separately from the other strategies. Not measured on real results yet.

**Your decision.** Confirmed alerts (the regular FINAL signal, IFVG signal, flip, retest and confirmed) end with three choices and, in Termux, three notification buttons that run `python decide.py <id> enter|wait|ignore`: ENTER prints the entry, stop, targets and R:R (you place the trade yourself) and records that you took it; WAIT prints the exact condition that will decide it; IGNORE records it and the bot stops alerting about that setup. Your choices are written to `decisions.csv` (the bot only reads it). The bot still never trades.

## The 4-condition confluence in every IFVG alert

Every IFVG alert (state flip, retest, confirmed, signal, invalidated) now shows `Confluence [X/4]`, the conditions completed, the conditions missing (or "none") and what the missing ones mean for the trade. The four conditions: H1 bias; a liquidity sweep first; a true body break (IFVG, not a wick); session killzone plus a closed confirmation candle (only counted once the closed M5 candle is inside the zone). Arabic with `ALERT_LANG=ar`. The regular engine signal keeps its own entry checklist.

## Structural Stop Loss (IFVG plan)
- Hard SL = behind the sweep/swing extreme of the last 24 M15 candles up to the break, plus 0.5 ATR (`IFVG_SL_ATR`). Never at the IFVG edge.
- Early Exit (structural invalidation) = a full M15 close beyond the IFVG far edge. It is separate from the Hard SL.
- Optimal Entry = 50%-75% depth of the IFVG (62.5% limit; market if the confirmation close is already in the band).
- R:R, the 2R gate and the cost check use the new wider stop. Tracking uses `market_entry` (limit orders wait for the fill).

## US mode (US100 / US30)
US100 and US30 are reviewed only inside the New York open window, default 09:30-12:00 New York time (`US_NY_WINDOW`, DST-aware). Only when `US_MODE=1` (default off): outside it the scan says WAIT and writes the reason to rejections.log. Structural stop, 4/4 confluence block and ENTER/WAIT/IGNORE options are the same as for the IFVG plan.

**US100 / US30 are ON again at any hour** (default SYMBOLS includes them). The New York window gate is OFF by default; `US_MODE=1` turns it on.

## Gold Asia-range breakout (`asia_breakout.py`, signals only)
XAUUSD only, closed M15 candles. Asia range 19:00-03:00 New York; a breakout candle must close 03:00-08:00 ET (nothing new after 09:30 ET).
Filters: range 1.0-3.0 x H1 ATR; body >= 50% of the candle; close >= 0.1 ATR beyond the edge; tick volume >= 1.2 x the 20-candle average; H1 structure trend agrees. Then: retest of the edge, and a closed M15 confirmation candle. Two closes back inside = fake breakout (cancelled); > 1 ATR beyond the breakout candle without a retest = missed.
Plan: Hard SL behind the retest extreme +/- 0.5 ATR (`ASIA_SL_ATR`), Early Exit = an M15 close back inside the range, TP1 1R (stop to break-even), TP2 2R (the tracked target), TP3 3R or the next liquidity. Goes through the same cost check, one-signal-per-market lock and ENTER/WAIT/IGNORE options as the IFVG signal. The DXY filter is not used (no feed). `ASIA_BREAKOUT=0` turns it off. All numbers are starting hypotheses: measure it with `python journal.py profile` (profile `asia`) before trusting it.

## Optional second data source: Twelve Data (US100 / US30)
Set `TWELVEDATA_API_KEY` (your own free key, never commit it) and US100 / US30 get `twelvedata:NDX` / `twelvedata:DJI` as a fallback after Yahoo fails (override names with `TWELVEDATA_SYMBOL_US100` / `TWELVEDATA_SYMBOL_US30`). The symbols and the free plan's coverage and request limits are NOT verified: check them on twelvedata.com first. It is a cash index, so its price differs from your broker and from the futures: run `calc_offset.py`. Without a key nothing changes.

## Arabic-only alerts
Arabic is OPT-IN: `ALERT_LANG=ar` (the default is English, `ALERT_LANG=en`). Notification titles, bodies, reasons, trade plan, confluence block, gate reasons and the ENTER/WAIT/IGNORE buttons (دخول / انتظار / تجاهل) are Arabic, and every alert ends with a short Arabic explanation (what it means and what to do). Tapping a notification opens the whole alert with its explanation as a text file (`alert_texts/`, newest 40 kept; needs `termux-open`). The spoken label uses `termux-tts-speak -l ar` (it needs an Arabic voice installed on the phone). Terminal stage lines and the market report stay as they were.

## Trades report (`daily_report.py`)
Every `TRADES_REPORT_MINUTES` (default 30, 0 = off) the bot prints an Arabic report: open signals (entered or waiting for the entry price, with the floating P/L in R), signals closed today, and the last 7 days (trades, wins / losses, net R). The same text updates ONE silent notification (tap = the whole report). `RISK_USD` (the money you risk per trade) adds approximate dollar amounts. On demand: `python daily_report.py`. These are the bot's followed signals, not your MT5 account; R is a unit of risk, and past results do not guarantee the future.

## Arabic on the Termux screen
Termux draws Arabic reversed and unjoined (a terminal limit; notifications are fine). `termfix.py` shapes Arabic text for the SCREEN only when `arabic-reshaper` and `python-bidi` are installed (`pip install arabic-reshaper python-bidi`); `ALERT_TERMINAL_BIDI=0` turns it off. The stage lines (STAGE ...) are screen-only and are always English. Notifications and the tap-to-open text are never shaped.

## Entry-zones report and setup quality (information only)
`zones_report.py` keeps the latest live setups (sweep -> MSS -> FVG/OB zone) per market and every `ZONES_REPORT_MINUTES` (default 30; 0 = off) prints and phones ONE replaced silent notification listing the zones near price. Each is graded STRONG / MEDIUM / WEAK from 5 checks (sweep, MSS with displacement, fresh zone, price in/near the zone, H1 agrees) with what is missing. The grade counts conditions; it is not a win probability and never a signal. No external AI is called (it would need a paid API).

## GBPUSD and USDJPY
Added to the default symbols (Yahoo `GBPUSD=X`, `USDJPY=X`), forex profile (H4/H1 bias, M15 confirmation), swing and scalp strategies, news currencies and the 17:00 New York FX day. GBPUSD also gets the dollar-context line. Specs in `markets.json` are unverified until you fill them from MT5. Remember: correlated pairs (EURUSD / GBPUSD) are one risk, not two. Remove them with `SYMBOLS=BTCUSD,XAUUSD,US100,US30,EURUSD,GBPJPY`.

## Stop Loss Engine (`stop_engine.py`, signals only)
Pure, typed functions: `hard_sl`, `early_invalidation`, `adjusted_lot_size`, `net_rr`, `breakeven_level` / `should_move_to_breakeven` and `plan()` that combines them. Anchor = sweep / pre-displacement swing extreme (never a zone edge); buffer = ATR multiple per asset class (forex majors 0.5, JPY crosses / gold / indices / crypto 0.7) + the spread; Hard SL alone sizes the lot (rounded down, risk capped at 2%); sweep + HTF = 100% risk, otherwise 50% with the same wide stop; net R:R to TP1 below 1:2 = "UNBALANCED RISK"; spread spike, structure too far, anchor on the zone edge or a lot below the minimum = REJECTED. Break-even is advice (entry + cost at TP1 or 1.5R); the bot never moves an order. It is a standalone, tested module: the live alerts still use the existing structural stop in `flip_ifvg.py` (0.5 ATR) until you decide to switch them over.

## Fibonacci golden zone (mandatory) and wider structural stop
- `forex` and `golden` profiles now REQUIRE the entry to sit in the golden zone of the latest M15 leg: **50% (the last test of the midpoint) to 79%**, in the trade's direction (`golden_lo` / `golden_hi` in `selective.py`). Other profiles still only show the `Fib:` line.
- Every `selective` stop is now structural (`structural_stop=True`): behind the farthest of the zone, the retest extreme and (reversal) the sweep extreme, plus the asset-class ATR buffer and the spread (`stop_engine.py`); never tighter than before. The quality / cost score is still judged on the old tight geometry so the wider stop does not make a setup look better. The ceiling for "stop too wide" is the asset class limit (4-4.5 M15 ATR). Expect fewer but better-protected signals. The setup also carries `early_exit` (zone edge: a full M15 close beyond it) and `risk_scale` (1.0 with a sweep and the HTF, 0.5 otherwise).
- Opt out per run: `make_config(..., structural_stop=False)` / `require_fib=False` (tests do).

## Displacement engine (`displacement_engine.py`, signals only, standalone)
A 4-state machine (IDLE -> IMPULSE -> RETEST -> COOLDOWN) for liquidity sweeps and momentum displacement on closed bars: HTF (H1) liquidity pools and EMA50 bias from previously closed bars only, LTF (M15) displacement (body >= 60% of the range, range >= 1.2 ATR, volume >= 1.3 x SMA20[1], an unfilled 3-candle FVG), expansion breakout (close beyond the level by 0.15 ATR, with the bias) or sweep reversal (wick beyond the pool, close back inside; against the bias it is flagged `counter_trend`, risk 50%). Hunting only in the New York killzones 02:00-05:00 and 08:00-11:00. Anti-chase 1.2 ATR, max 16 bars, two closes beyond the invalidation level. Plan: entry = confirmation close, Hard SL = retest extreme -/+ 0.5 ATR, TP1/TP2/TP3 = 1R/2R/3R, plus an early-invalidation alert. Every transition (SETUP, RETEST, CONFIRMED, INVALIDATED, MISSED) is a JSON event and a log line; the same bar is never processed twice. All thresholds are in `Config`. It is NOT wired into the live loop yet and has not been measured on real data. Volume is required by default: feeds without volume produce no signals (`volume_required=False` to relax).

### Displacement engine is now live in the alerts
`displacement_alerts.py` runs one engine per market inside `track_setups` (`DISPLACEMENT=0` turns it off). Alerts: DISP_SETUP, DISP_RETEST, DISP_SIGNAL (CONFIRMED ENTRY with the plan), DISP_INVALIDATED; MISSED only prints on screen. On start it silently replays the last 120 M15 bars and announces only the newest bar. Feeds without volume (Yahoo FX) skip the volume filter and the alert says so. These signals are alert-only: they are not opened in the signal store, so the trades report and the tracker do not count them.

## Skills as bot commands (information only, no external AI)
- `python doctor.py`: why is something missing? Checks the code version, a `SYMBOLS` / `ALERT_LANG` override in the shortcut / `.bashrc` / `.profile`, Termux:API, the Twelve Data key (never printed), `BALANCE`.
- `python review.py [profile|symbol|session|side]`: performance review from the journal: trades, R, average R, win rate and a verdict per group; under 30 trades = "too early". Displacement alerts are alert-only and not in the journal.
- Lot size in the displacement SIGNAL: set `BALANCE` (and optionally `RISK_PCT`, max 2%); the specs of the symbol must be filled in `markets.json` (`python set_spec.py`). 50% risk against the H1 bias. Without them the alert says exactly what is missing.

## Three alert-only detectors (`session_alerts.py`)
All closed candles, no orders, English alerts, one pass per closed bar. Switches: `SESSION_ALERTS=0` (all), `SCALPER=0`, `SMC_PRIME=0`, `PULSE=0`. Every threshold is in `session_alerts.Config`.
- **ICT Session Scalper** (M5, 10:00-11:00 New York): a sweep of the session range / a swing, then a BOS, then an FVG inside the impulse. Entry = FVG midpoint (`entry_mode="edge"` for the boundary), SL behind the sweep extreme + the asset-class ATR buffer, TP1 = the nearest opposing liquidity (rejected if R:R < 1:2), TP2 = the next pool or 3R. One signal per direction per day; INVALIDATED when a candle closes through the whole FVG, MISSED when the window ends untouched.
- **SMC Prime** (M15 + the H1 trend): needs 2+ of {liquidity sweep, CHoCH/BOS, price in an untested order block} AND the H1 trend. Entry = the OB edge, SL behind the sweep / OB extreme + ATR buffer, TP1 = 2R (stop to break-even), TP2 = the next H1 liquidity (or 3R), invalidation = a full M15 close beyond the OB.
- **Order Flow Pulse (ESTIMATE)** (M5): volume >= 1.8 x SMA20[1] and body >= 60% of the range. It is an estimate from candle volume, not real order flow, and feeds without volume (Yahoo FX) produce nothing. SL beyond the pulse candle (`pulse_sl_mode="mid"` for its midpoint), TP = an ATR multiple (never below 1R / 2R), invalidated by the next candle (engulfing or back inside the previous bar). A pulse in the same direction within 3 bars adds a "confirms" line to the other two alerts. Cooldown 6 bars.
Pending scalper / pulse checks are lost on restart. Not measured on real data; the alerts are not in the journal.

### Session detectors: persistence, measurement, no-volume feeds
- **Restart-safe:** pending scalper setups, sent keys and pulse cooldowns are saved in `session_state.json` (folder `SESSION_DIR`, default the project folder).
- **Measured:** `session_tracker.py` follows every SIGNAL of the three detectors on closed M5 bars (entry touch for limit entries, then stop = -1R or TP1 = +R:R; both in one bar = the stop first; unfilled after 4 h = not counted). Results go to `session_results.csv` and a `RESULT: ...` line appears on the screen. `python review.py` prints them per detector with the 30-trade rule. It is an approximation from M5 candles (no ticks, no spread).
- **No volume (Yahoo FX):** Order Flow Pulse falls back to a candle-range spike (>= 1.8x the 20-bar average) and says "range proxy" in the alert. `pulse_range_proxy=False` turns the fallback off.

### Zones report sound
The entry-zones notification is sent only when there are zones, and silently (`--alert-once`); `ZONES_SOUND=1` allows the sound. "No ready zones" is printed on the screen only.


## Kenan Trader AI v2 (branch `kenan-v2`) — signals only, never executes trades
- Six markets, each independent: XAUUSD, NAS100 (US100), US30, BTCUSD, USDJPY, GBPUSD. H4/H1 = context, **M15 = the only entry timeframe**.
- Mandatory chain: HTF context → liquidity → sweep → CHoCH/MSS → displacement → FVG/OB → retest → closed M15 confirmation → room + R:R ≥ 1:2 → spread/news/session filters. Quality Score ≥ 80/100 (a missing mandatory condition is never compensated; it is not a win probability).
- Levels: SL behind the structural invalidation + ATR/spread buffer; TP1 = 1R (move to break-even by hand), TP2 = 2R. `session_engine.py` holds the per-market session table (DST aware).
- One active signal per market (`signal_store` + `signal_manager.py`; claims survive restarts; `bot.lock` stops a second copy of the bot).
- Telegram: full "KENAN TRADER AI" message for signals (`telegram_alerts.py`); preparatory alerts are sent silently and labelled "NOT an entry signal". `TELEGRAM_FORMAT=classic` keeps the old text.
- Specs/spread are entered by hand (`markets.json`, `set_spec.py`, `SPREAD_<SYMBOL>=...`): MT5 data is not available on Android. Data: Yahoo/Binance/Twelve Data.
- `python review.py [symbol|session|side|score]`: win rate, TP1/TP2 hit, SL rate, avg R, expectancy, PF, max drawdown, losing streak. Fewer than 30 trades = too early; forward testing only; no profit is guaranteed.
- `KENAN_V2=0` restores the old behaviour.

- SMC Prime, ICT Session Scalper and Order Flow Pulse alerts are ON by default (alert-only, session table + one signal per market apply). Signal classes play the tone; switch off with SMC_PRIME=0 / SCALPER=0 / PULSE=0. Pulse is a volume estimate, not real order flow.

- The trades report now has a block for SMC Prime / ICT Session Scalper / Order Flow Pulse: signals today, in trade, waiting for entry, closed today (W/L, R). Approximate (closed M5 bars), alert-only, not your real MT5 account.

- SMC Prime / Scalper / Pulse alerts are no longer blocked by the main strategy's active signal or by each other (they de-duplicate per zone and are tracked separately); the displacement alerts still obey one-signal-per-market.

- SMC Prime / Scalper / Pulse alerts work on every scanned market (also EURUSD, GBPJPY, DAX40 when added to SYMBOLS) and are not limited by the main strategy session table.

- Default scan list: the six Kenan markets (main strategy) plus EURUSD, GBPJPY, DAX40 (only the SMC Prime / Scalper / Pulse alerts apply there). Use SYMBOLS=... to change it.

- Conflict check (`conflict_check.py`): an SMC Prime / Scalper / Pulse signal against the H1 trend or with an opposing entry zone on the way to TP1 is still shown but carries a "CONFLICT" warning; Pulse always carries the "estimate" note. Information only.

## Unified Trade Confirmation Engine (`unified_engine.py`, signals only)
- The ONLY place that issues CONFIRMED BUY / CONFIRMED SELL. Hierarchy: Level 1 SMC Prime = structure & direction (35), Level 2 ICT Session Scalper = liquidity & entry timing (35), Level 3 Order Flow Pulse = momentum (30, built from candle volume/range, NOT real order flow).
- Order of decision: any direction conflict = NO TRADE whatever the score; the six mandatory conditions (liquidity sweep, CHoCH/MSS, displacement, FVG/OB, retest, closed M15 confirmation) come from the main SELECTIVE chain and are never replaced by points; then Level 1, 2, 3 must each confirm; then score >= 80 (weight x 3 objective checks per module; counts conditions, not a win probability). Each module may answer WAIT.
- Only closed candles (a vote on an unfinished candle is refused), fresh votes (SMC 3 h, Scalper 2 h, Pulse 1 h, main 6 h) and a non-lagging data feed count.
- ONE confirmation per main signal id, kept across restarts (`unified_state.json`). It is valid `UNIFIED_VALID_MIN` minutes (default 90), then EXPIRED; it is CANCELLED when a full M15 candle closes beyond its invalidation level or a module turns against it.
- The main SELECTIVE chain and the three modules no longer alert with a tone by themselves: the main chain shows as a CANDIDATE (screen only) and the modules as silent information. `UNIFIED=0` restores the old behaviour; `UNIFIED_QUIET_MODULES=0` gives the module tones back.
- Run: the bot does it by itself (`ICT-Alerts`). Board: `python unified_board.py`. Default risk text 0.5% (`RISK_PERCENT`).
- Seven markets: XAUUSD, US100, US30, BTCUSD, GBPUSD, USDJPY, EURUSD. No backtest or forward test has been done: no success rate is claimed.

- Fibonacci (50-79% golden zone of the latest M15 leg) is a condition of the unified confirmation: all three modules must have their entry inside it. It may be WAIVED only on a strong confirmation: every module check passed (score 100), H4 and H1 both agree, main chain score 90+ and R:R >= 1:2; the alert then says "Fibonacci: WAIVED ...". A conflict or a missing mandatory condition is never waived. FIB_GATE=0 switches the condition off, FIB_WAIVER=0 switches the waiver off, FIB_LO / FIB_HI change the zone.

## Professional Trade Analysis Engine (`trade_analysis.py`, signals only)
- A fourth, independent brain. It does not change SMC Prime, Order Flow Pulse or ICT Session Scalper: it re-reads CLOSED H4 / H1 (direction, liquidity) and M15 (entry confirmation) candles of the same feed and the same offsets, with SMA/EMA 50 and 200, RSI, Bollinger Bands, MACD, support/resistance, Fibonacci and market structure, plus the main chain's sweep / CHoCH-MSS / FVG-OB / retest / M15-confirmation facts.
- Confidence Score /100: trend 20, M15 entry 15, liquidity+zone 15, momentum 15, location 10, room before the opposing S/R 15, Fibonacci 10. Minimum `ANALYSIS_MIN` (80). It counts conditions, it is not a win probability.
- It is the LAST gate of a unified confirmation: no final signal when the data is incomplete (M15 60+, H1 210+ for the 200 averages, H4 60+ closed candles) or stale, when the score is below 80, when R:R to TP2 is below 1:2, or when REAL order flow is against the trade. Then the decision stays WAIT and is re-checked on the next scan.
- Plan in the alert: BUY/SELL, entry, stop (the main chain's structural stop, never tightened), TP1 (1R), TP2, TP3 (next support/resistance beyond TP2, else a labelled 3R extension), R:R, reasons, cautions, a rating of each strategy (ALIGNED / AGAINST), cancellation conditions.
- Order Flow is used only when real data is supplied; the free feeds have none, so it is reported "unavailable" and earns no points. Nothing is invented.
- `ANALYSIS=0` switches the engine off (the unified engine then works as before). Voice (text-to-speech) is spoken for SMC Prime alerts only.

- Order Flow Pulse, ICT Session Scalper and SMC Prime keep their own notification, tone and vibration (voice: SMC Prime only). The alert says the final CONFIRMED BUY / SELL comes only from the unified engine. UNIFIED_QUIET_MODULES=1 makes them silent information.
