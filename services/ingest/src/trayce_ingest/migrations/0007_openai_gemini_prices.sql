-- OpenAI and Google Gemini list prices (standard tier, text), checked 2026-10-29 against
-- developers.openai.com/api/docs/pricing and ai.google.dev/gemini-api/docs/pricing.
--
-- Lookup is by exact model name, and OTel instrumentation reports `gen_ai.response.model`, which
-- for OpenAI is the dated snapshot when a model has one, so each snapshot gets its own row.
-- gpt-4o-2024-05-13 has its own, higher price and is left out rather than priced as gpt-4o.
--
-- Cache writes: GPT-5.6 and later charge 1.25x input for the default 30-minute write and 2.5x for
-- a 12-hour one; they go in the 5-minute and 1-hour columns, the short and long TTL. Older OpenAI
-- models and Gemini charge nothing per written token (Gemini bills cache storage by the hour,
-- which isn't a span's cost), so their write prices are 0, not null, or every cached call's cost
-- would be unknown.
--
-- Prompt-length tiers aren't modeled: gpt-6.x, gpt-5.6, gpt-5.5, and gpt-5.4 are priced at their
-- rate for input up to 272K tokens, gemini-3.1-pro-preview and gemini-2.5-pro at their rate for
-- prompts up to 200K, so a longer prompt is underpriced. Audio input is priced as text.
INSERT INTO model_prices (model, input_per_mtok, output_per_mtok, cache_read_per_mtok,
                          cache_write_5m_per_mtok, cache_write_1h_per_mtok, effective_from) VALUES
    ('gpt-6-astra',             10.00, 50.00, 1.00,  12.50, 25.00, '2026-01-01T00:00:00Z'),
    ('gpt-6.1-sol',              2.00, 10.00, 0.10,   2.50,  5.00, '2026-01-01T00:00:00Z'),
    ('gpt-6.1-terra',            1.00,  5.00, 0.05,   1.25,  2.50, '2026-01-01T00:00:00Z'),
    ('gpt-6-sol',                2.00, 10.00, 0.20,   2.50,  5.00, '2026-01-01T00:00:00Z'),
    ('gpt-6-luna',               0.10,  0.50, 0.01,  0.125,  0.25, '2026-01-01T00:00:00Z'),
    -- gpt-5.6-sol is a promotional price, "available at least through November 21, 2026".
    ('gpt-5.6-sol',              4.00, 20.00, 0.40,   5.00, 10.00, '2026-01-01T00:00:00Z'),
    ('gpt-5.6-terra',            2.00, 12.00, 0.20,   2.50,  5.00, '2026-01-01T00:00:00Z'),
    ('gpt-5.6-luna',             0.20,  1.20, 0.02,   0.25,  0.50, '2026-01-01T00:00:00Z'),
    ('gpt-5.5',                  5.00, 30.00, 0.50,      0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5.5-2026-04-23',       5.00, 30.00, 0.50,      0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5.4',                  2.50, 15.00, 0.25,      0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5.4-2026-03-05',       2.50, 15.00, 0.25,      0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5.4-mini',             0.75,  4.50, 0.075,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5.4-mini-2026-03-17',  0.75,  4.50, 0.075,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5.4-nano',             0.20,  1.25, 0.02,      0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5.4-nano-2026-03-17',  0.20,  1.25, 0.02,      0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5.2',                  1.75, 14.00, 0.175,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5.2-2025-12-11',       1.75, 14.00, 0.175,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5.1',                  1.25, 10.00, 0.125,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5.1-2025-11-13',       1.25, 10.00, 0.125,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5',                    1.25, 10.00, 0.125,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5-2025-08-07',         1.25, 10.00, 0.125,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5-mini',               0.25,  2.00, 0.025,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5-mini-2025-08-07',    0.25,  2.00, 0.025,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5-nano',               0.05,  0.40, 0.005,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-5-nano-2025-08-07',    0.05,  0.40, 0.005,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-4.1',                  2.00,  8.00, 0.50,      0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-4.1-2025-04-14',       2.00,  8.00, 0.50,      0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-4.1-mini',             0.40,  1.60, 0.10,      0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-4.1-mini-2025-04-14',  0.40,  1.60, 0.10,      0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-4.1-nano',             0.10,  0.40, 0.025,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-4.1-nano-2025-04-14',  0.10,  0.40, 0.025,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-4o',                   2.50, 10.00, 1.25,      0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-4o-2024-08-06',        2.50, 10.00, 1.25,      0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-4o-2024-11-20',        2.50, 10.00, 1.25,      0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-4o-mini',              0.15,  0.60, 0.075,     0,     0, '2026-01-01T00:00:00Z'),
    ('gpt-4o-mini-2024-07-18',   0.15,  0.60, 0.075,     0,     0, '2026-01-01T00:00:00Z'),
    ('o3',                       2.00,  8.00, 0.50,      0,     0, '2026-01-01T00:00:00Z'),
    ('o3-2025-04-16',            2.00,  8.00, 0.50,      0,     0, '2026-01-01T00:00:00Z'),
    ('o4-mini',                  1.10,  4.40, 0.275,     0,     0, '2026-01-01T00:00:00Z'),
    ('o4-mini-2025-04-16',       1.10,  4.40, 0.275,     0,     0, '2026-01-01T00:00:00Z'),

    -- Gemini's announced increases get their own rows, effective at 00:00 UTC on the first day.
    ('gemini-4-argon',               2.00, 10.00, 0.10,  0, 0, '2026-01-01T00:00:00Z'),
    ('gemini-4-argon',               4.00, 20.00, 0.20,  0, 0, '2026-12-01T00:00:00Z'),
    ('gemini-3.8-flash',             0.75,  3.75, 0.075, 0, 0, '2026-01-01T00:00:00Z'),
    ('gemini-3.8-flash',             1.50,  7.50, 0.15,  0, 0, '2027-01-01T00:00:00Z'),
    ('gemini-3.6-flash',             0.75,  3.75, 0.075, 0, 0, '2026-01-01T00:00:00Z'),
    ('gemini-3.6-flash',             1.50,  7.50, 0.15,  0, 0, '2027-01-01T00:00:00Z'),
    ('gemini-3.5-flash-lite',        0.30,  2.50, 0.03,  0, 0, '2026-01-01T00:00:00Z'),
    ('gemini-3.1-pro-preview',       2.00, 12.00, 0.20,  0, 0, '2026-01-01T00:00:00Z'),
    ('gemini-3.1-pro-preview-customtools', 2.00, 12.00, 0.20, 0, 0, '2026-01-01T00:00:00Z'),
    ('gemini-3.1-flash-lite',        0.25,  1.50, 0.025, 0, 0, '2026-01-01T00:00:00Z'),
    ('gemini-3-flash-preview',       0.50,  3.00, 0.05,  0, 0, '2026-01-01T00:00:00Z'),
    ('gemini-2.5-pro',               1.25, 10.00, 0.125, 0, 0, '2026-01-01T00:00:00Z'),
    ('gemini-2.5-flash',             0.30,  2.50, 0.03,  0, 0, '2026-01-01T00:00:00Z'),
    ('gemini-2.5-flash-lite',        0.10,  0.40, 0.01,  0, 0, '2026-01-01T00:00:00Z')
ON CONFLICT DO NOTHING;
