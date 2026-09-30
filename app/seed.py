"""Demo project seeded on first run so the UI is never empty.

Timeline (dimmer lanes; both ramps a and d are cut short):

  L1 dimmer:  0 --1000..5000 ramps 0->200 (cut at 1500) -- jump 255 --3000->100
  L2 dimmer:  0 --2000..6000 ramps 0->180 (cut at 4000) ->40 over 2000ms
  L3 dimmer:  0 --7000 jump 160, hold 500ms, then ramps to 0 over 2000ms
"""

DEMO_ID = "demo"

DEMO_PROJECT = {
    "name": "演示工程",
    "cues": [
        {"id": "a", "fixture": "L1", "channel": "dimmer", "target": 200,
         "duration": 4000, "start": {"mode": "absolute", "at": 1000}},
        {"id": "b", "fixture": "L1", "channel": "dimmer", "target": 255,
         "duration": 0,
         "start": {"mode": "absolute", "at": 1500}},
        {"id": "c", "fixture": "L1", "channel": "dimmer", "target": 100,
         "duration": 1000,
         "start": {"mode": "reference", "refId": "b",
                   "anchor": "end", "offset": 1500}},
        {"id": "d", "fixture": "L2", "channel": "dimmer", "target": 180,
         "duration": 4000,
         "start": {"mode": "reference", "refId": "a",
                   "anchor": "start", "offset": 1000}},
        {"id": "e", "fixture": "L2", "channel": "dimmer", "target": 40,
         "duration": 2000,
         "start": {"mode": "reference", "refId": "b",
                   "anchor": "end", "offset": 2500}},
        {"id": "f", "fixture": "L3", "channel": "dimmer", "target": 160,
         "duration": 0, "start": {"mode": "absolute", "at": 7000}},
        {"id": "g", "fixture": "L3", "channel": "dimmer", "target": 0,
         "duration": 2000,
         "start": {"mode": "reference", "refId": "f",
                   "anchor": "end", "offset": 500}},
    ],
}
