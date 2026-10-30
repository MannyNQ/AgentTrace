### ❌ AgentTrace eval: 1 regression

`support` v2 · 15 items × 3 samples · [this run](http://127.0.0.1:13000/p/default/experiments/440068ae-291a-4331-aaab-8c338504cbbe) vs [baseline](http://127.0.0.1:13000/p/default/experiments/353eef3f-22b2-4c3a-a74d-38d3b3225e1c) (at the merge-base `27a9da5`)

| Scorer | Baseline | Current | Change (95% CI) | max_drop | Items | Status |
|---|---|---|---|---|---|---|
| `answer` | 100% | 100% | +0 pts (+0 pts to +0 pts) | 5 pts | 3 | ✅ ok |
| `steps` | 93% | 33% | -60 pts (-87 pts to -33 pts) | 5 pts | 15 | ❌ regressed |
| `few-steps` | 100% | 100% | +0 pts (+0 pts to +0 pts) | 5 pts | 15 | ✅ ok |

⚠️ Underpowered: the interval is wider than `max_drop`, so a drop that size could go undetected. More items or `samples` narrow it.

Cost per sample $0.0139 → $0.0138 · p50 latency 5.0 s → 4.9 s (not gated)

**Item regressions** (most samples passed before, most fail now; not gated)

- [item `f5fbea25`](http://127.0.0.1:13000/p/default/experiments/440068ae-291a-4331-aaab-8c338504cbbe/compare/353eef3f-22b2-4c3a-a74d-38d3b3225e1c/items/f5fbea25-0c80-4ca9-a843-efbfd8e05f06) · `steps` 3/3 → 0/3
- [item `56518cd4`](http://127.0.0.1:13000/p/default/experiments/440068ae-291a-4331-aaab-8c338504cbbe/compare/353eef3f-22b2-4c3a-a74d-38d3b3225e1c/items/56518cd4-cb33-4c81-95ad-6239f6b00730) · `steps` 3/3 → 0/3
- [item `b84997bb`](http://127.0.0.1:13000/p/default/experiments/440068ae-291a-4331-aaab-8c338504cbbe/compare/353eef3f-22b2-4c3a-a74d-38d3b3225e1c/items/b84997bb-9683-412b-8eaf-43c206837498) · `steps` 3/3 → 0/3
- [item `bd404c1d`](http://127.0.0.1:13000/p/default/experiments/440068ae-291a-4331-aaab-8c338504cbbe/compare/353eef3f-22b2-4c3a-a74d-38d3b3225e1c/items/bd404c1d-513a-4b5e-b8aa-570e159173f6) · `steps` 3/3 → 0/3
- [item `ab7bbbbe`](http://127.0.0.1:13000/p/default/experiments/440068ae-291a-4331-aaab-8c338504cbbe/compare/353eef3f-22b2-4c3a-a74d-38d3b3225e1c/items/ab7bbbbe-22d2-4294-aac4-de77081a3ef9) · `steps` 3/3 → 0/3
- [item `8cfb8b8a`](http://127.0.0.1:13000/p/default/experiments/440068ae-291a-4331-aaab-8c338504cbbe/compare/353eef3f-22b2-4c3a-a74d-38d3b3225e1c/items/8cfb8b8a-e621-4b49-b2c4-77e6179b1bcb) · `steps` 3/3 → 0/3
- [item `0874e921`](http://127.0.0.1:13000/p/default/experiments/440068ae-291a-4331-aaab-8c338504cbbe/compare/353eef3f-22b2-4c3a-a74d-38d3b3225e1c/items/0874e921-7595-4b5a-a5c9-e4856f2a9bd8) · `steps` 3/3 → 0/3
- [item `8a978d2c`](http://127.0.0.1:13000/p/default/experiments/440068ae-291a-4331-aaab-8c338504cbbe/compare/353eef3f-22b2-4c3a-a74d-38d3b3225e1c/items/8a978d2c-1d4b-4c2c-8c73-ec424588868c) · `steps` 3/3 → 0/3
- [item `9c1cb2af`](http://127.0.0.1:13000/p/default/experiments/440068ae-291a-4331-aaab-8c338504cbbe/compare/353eef3f-22b2-4c3a-a74d-38d3b3225e1c/items/9c1cb2af-d47b-426a-8edb-6f1ea63169a9) · `steps` 3/3 → 0/3
