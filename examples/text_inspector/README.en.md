<!-- source: 259b2c67ed10 -->
# Text structure analysis Agent

It really processes the input text and returns character count, line count, paragraph count, word frequency, a short summary and an input digest. English is counted by word and Chinese by character; no external model or API is used.

Run it with `python examples/text_inspector/agent.py --port 9000`.

It serves `/invoke` over HTTP JSON with the skill `inspect`. Example input: `{"text":"Hello agent.\n\nHello market."}`. `/counts` returns the real request count, which business acceptance uses to check whether a duplicate request ran twice.

`scripts/local_business_acceptance.py` starts this service explicitly, then lists and calls it through the four nodes' management interfaces. An ordinary SDK start does not load this offering.

Under marketplace rules the first ten free technical deliveries are published as redacted samples. Use text fit for public sampling in those calls.

So the console can render one field per input, add this to the skill declaration:

```json
{"id":"inspect","name":"Analyse text","input_schema":{"type":"object","properties":{"text":{"type":"string","title":"Text to analyse","minLength":1,"maxLength":20000}},"required":["text"]}}
```

The form currently supports up to 32 string, integer, number, boolean or enum fields, plus required, range, length and default; nested schemas, composition and references fall back to the JSON editor automatically. Large integers the browser cannot represent exactly should be agreed with the service as strings. Changing the input requires a fresh quote; an order already accepted keeps its original input.
