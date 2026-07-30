# Pace terminology and inference

PromptFit separates workout-language interpretation from pace calculation. The language model identifies the workout structure and preserves the coach's intensity wording; `webapp/pace_knowledge.py` then resolves the wording against the athlete's saved pace profile. This keeps FIT targets deterministic and makes the interpreted JSON auditable.

## Precedence

1. An explicit pace in the workout, such as `6:40/mi` or `4:10/km`.
2. The athlete's exact saved anchor for that intensity.
3. An equivalent race pace inferred from the athlete's saved race results/paces.
4. A central terminology estimate from the closest strong anchor.
5. No rigid target when the cue is inherently terrain-, duration-, or effort-dependent.

Adding a new anchor never deletes another anchor. If the athlete enters both 10K and threshold pace, threshold uses the entered threshold pace. If only 10K pace is known, threshold is estimated at about 97% of 10K speed, making it slightly slower than 10K pace. Race equivalence uses a Riegel exponent of 1.06 and combines multiple strong anchors with a median so one unusual result does not dominate every estimate.

## Supported athlete anchors

The web and iPhone interfaces expose easy, marathon, half marathon, lactate threshold/T, 10K, 5K, 3K, and mile/repetition pace. The API additionally normalizes common aliases such as `MP`, `HMP`, `LT`, `LT2`, `CV`, `I pace`, `R pace`, `vVO2max`, and `MAS`.

## Vocabulary behavior

| Family | Terms understood | Default interpretation when no exact anchor exists |
| --- | --- | --- |
| Recovery | recovery jog/run, very easy, shakeout, regeneration | Easier than normal easy running |
| Easy aerobic | easy, conversational, relaxed, E pace, Z2 when the zone system is clear | Easy/conversational |
| Aerobic support | general aerobic, GA, easy-to-moderate, endurance, medium-long, long-run pace | Between easy and steady |
| Upper aerobic | steady, moderate, steady-state, LT1, AeT, aerobic threshold | Clearly slower than LT2 |
| Race support | MP, HMP, 10-mile, 15K, 10K, 8K, 5K, 3K, mile effort | Named race-equivalent pace |
| Threshold | lactate/anaerobic threshold, LT/LT2, T pace, cruise intervals, one-hour pace, MLSS, comfortably hard, tempo in ordinary context | About one-hour race effort; usually a little slower than 10K for a sub-60-minute 10K runner |
| Sub-threshold | controlled/sub-threshold, Norwegian/double threshold | A little slower than LT2 |
| Critical velocity | CV, critical velocity/speed | Roughly 30–40-minute race effort; distinct from LT |
| VO2max | I pace, interval pace, VO2max, vVO2max, MAS | Roughly 3K–5K effort, modified by rep duration |
| Repetition | R pace, repetition/rep pace, mile effort | Fast relaxed repetitions with generous recovery |
| Effort-only | strides, hills, sprints, all-out, surges | No flat-ground watch pace unless the athlete explicitly supplied one |

### Coach-specific rules

- Daniels: E, M, T, I, and R retain their distinct meanings. T is approximately one-hour effort; I and R are not synonyms.
- Pfitzinger: recovery, general aerobic, endurance/medium-long/long, lactate threshold, VO2max, speed, and marathon-pace work are distinguished. Total session mileage is not mistaken for tempo-repetition mileage.
- Canova: regeneration and fundamental work are event-relative; specific work is at goal-event pace; specific extensive/intensive work sits just below/above it. `Special` and `special block` do not receive a single invented pace because special work can support race pace from either side.
- Hansons: marathon-plan `tempo` means goal marathon pace; `strength` is commonly about 10 seconds per mile faster than goal marathon pace. These meanings are only applied with Hansons context.
- Tinman/Schwartz: CV is distinct from threshold, while Tinman tempo/easy tempo is slower than threshold.
- McMillan and Lydiard labels are interpreted with their named system and workout context; ambiguous effort language is preserved rather than forced into a universal zone.

Numbered zones are intentionally conservative because three-zone, five-zone, heart-rate, lactate, and watch-vendor systems do not share boundaries. Weather, hills, altitude, surface, fatigue, and workout duration can also make effort a better prescription than pace.

## Reference background

- [England Athletics: simplifying training-pace jargon](https://www.englandathletics.org/news/simplifying-the-running-jargon-training-pace-2/)
- [World Athletics: building aerobic fitness](https://worldathletics.org/personal-best/performance/how-build-aerobic-fitness-tips-advice-running)
- [Luke Humphrey: Hansons-style tempo workouts](https://www.outsideonline.com/running/training/workouts/tempo-workouts-the-hansons-way/)
- [Renato Canova marathon training material](https://uploads.teachablecdn.com/attachments/DaSgVzVqRWak5Z1YXog8_Renato_Canova___Marathon.pdf)

These relationships are training estimates, not lab measurements. Direct athlete anchors remain authoritative.
