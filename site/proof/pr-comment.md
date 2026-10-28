### ✅ AgentTrace eval: no regressions

`support` v1 · 4 items × 3 samples · [this run](http://127.0.0.1:13000/p/default/experiments/f45dbb1a-c695-4398-8e70-a53004b923a5) vs [baseline](http://127.0.0.1:13000/p/default/experiments/8e29c61d-83a8-4824-a94d-721341dc3802) (at the merge-base `27a9da5`)

| Scorer | Baseline | Current | Change (95% CI) | max_drop | Items | Status |
|---|---|---|---|---|---|---|
| `answer` | 100% | 100% | +0 pts (+0 pts to +0 pts) | 5 pts | 2 | ✅ ok |
| `steps` | 75% | 25% | -50 pts (-100 pts to +0 pts) | 5 pts | 4 | ⚠️ underpowered |
| `few-steps` | 100% | 100% | +0 pts (+0 pts to +0 pts) | 5 pts | 4 | ✅ ok |

⚠️ Underpowered: the interval is wider than `max_drop`, so a drop that size could go undetected. More items or `samples` narrow it.

Cost per sample $0.0133 → $0.0140 · p50 latency 5.3 s → 4.6 s (not gated)

**Item regressions** (most samples passed before, most fail now; not gated)

- [item `f5fbea25`](http://127.0.0.1:13000/p/default/experiments/f45dbb1a-c695-4398-8e70-a53004b923a5/compare/8e29c61d-83a8-4824-a94d-721341dc3802/items/f5fbea25-0c80-4ca9-a843-efbfd8e05f06) · `steps` 3/3 → 0/3
- [item `56518cd4`](http://127.0.0.1:13000/p/default/experiments/f45dbb1a-c695-4398-8e70-a53004b923a5/compare/8e29c61d-83a8-4824-a94d-721341dc3802/items/56518cd4-cb33-4c81-95ad-6239f6b00730) · `steps` 3/3 → 0/3
