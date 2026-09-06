# Nemotron 550B Usage Guidelines — Token Optimization Strategy
**Created:** 2026-08-06 05:46 UTC  
**Purpose:** Offload heavy reasoning to Nemotron 550B, save main agent tokens

---

## 🎯 WHEN TO USE NEMOTRON

### ✅ ALWAYS Use Nemotron For:

1. **Complex data analysis** (>20 data points)
   - Trading performance audits
   - Multi-signal pattern analysis
   - Statistical validation
   - Anomaly detection

2. **Decision validation** (high-stakes choices)
   - Deploy vs revert decisions
   - Strategy changes
   - Risk assessment
   - Cost-benefit analysis

3. **Deep reasoning tasks**
   - Root cause analysis (failures/bugs)
   - Trade-off evaluation
   - Multi-factor optimization
   - Hypothesis testing

4. **Large dataset processing**
   - Trade history analysis (>50 trades)
   - Position audits (>10 positions)
   - Performance comparisons
   - Trend identification

### ⚠️ DON'T Use Nemotron For:

1. **Simple lookups** (read file, check status)
2. **Quick calculations** (basic math, counts)
3. **Code execution** (terminal commands, file operations)
4. **Real-time monitoring** (too slow, 60-90s latency)
5. **Interactive conversations** (main agent better for back-and-forth)

---

## 📋 USAGE PATTERN

### Standard Workflow

```python
# 1. Collect data locally (fast tools)
data = {
    "problem": "...",
    "metrics": {...},
    "options": {...}
}

# 2. Save to temp file
with open('/tmp/analysis_data.json', 'w') as f:
    json.dump(data, f)

# 3. Offload to Nemotron
result = terminal(
    f"timeout 90 python3 ~/.hermes/profiles/comboane-media/scripts/analyze_with_nemotron.py "
    f"'Your analysis task here' "
    f"'/tmp/analysis_data.json' "
    f"2>&1"
)

# 4. Parse result and act
analysis = json.loads(result['output'])
recommendation = analysis.get('analysis', '')
```

### Compact Data Format

**Keep data <5KB for faster response:**

```python
# ✅ GOOD — compact, focused
data = {
    "problem": "Brief description",
    "key_metrics": {"metric1": value1, "metric2": value2},
    "options": {"A": "...", "B": "..."},
    "question": "Specific question?"
}

# ❌ BAD — too verbose
data = {
    "full_trade_history": [...],  # 500KB
    "all_positions": [...],        # 100KB
    "raw_logs": "..."              # 200KB
}
```

---

## 🎯 USE CASES FROM TODAY

### Example 1: Bad Signal Blocking Decision

**Task:** Analyze 14 signals, recommend solution

**Data sent:**
```json
{
  "net_loss_per_day": 55.90,
  "winners_missed": 65.49,
  "losers_saved": 9.12,
  "options": {
    "A": "disable (2min)",
    "B": "smart exemptions (30min)"
  }
}
```

**Nemotron output:**
- Statistical validation (p<0.05)
- Clear recommendation (Option B)
- Quantitative reasoning
- **Saved:** ~10,000 tokens in main agent

---

## 💰 TOKEN ECONOMICS

### Cost Comparison

**Main Agent (Claude Sonnet):**
- Input: $3 per 1M tokens
- Output: $15 per 1M tokens
- Average task: 5,000 tokens = $0.015

**Nemotron 550B (via 9router):**
- Flat rate or usage-based
- Heavy reasoning offloaded
- Main agent stays lean

**Break-even:** Any analysis >2,000 tokens input

### Token Savings Example (Today's Work)

**Without Nemotron:**
- 14-signal analysis: ~8,000 tokens
- Statistical reasoning: ~3,000 tokens
- Decision framework: ~2,000 tokens
- **Total:** ~13,000 tokens = $0.04

**With Nemotron:**
- Preparation: 1,000 tokens
- Call: (Nemotron pays)
- Parse result: 500 tokens
- **Total:** ~1,500 tokens = $0.005

**Savings:** 85% token reduction on analysis tasks

---

## 🔧 IMPLEMENTATION CHECKLIST

### Before Each Session

- [ ] Check NINE_ROUTER env vars set
- [ ] Nemotron script accessible
- [ ] Test with simple query if unsure

### During Analysis

- [ ] Identify heavy reasoning tasks
- [ ] Prepare compact data (<5KB)
- [ ] Use 90s timeout (Nemotron can be slow)
- [ ] Parse and validate output

### After Analysis

- [ ] Log Nemotron usage (for tracking)
- [ ] Document key insights
- [ ] Archive data if repeatable pattern

---

## 📊 TRACKING TEMPLATE

```python
# Add to analysis scripts
nemotron_log = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "task": "Brief description",
    "data_size": len(json.dumps(data)),
    "tokens_saved_estimate": 5000,
    "recommendation": "Key insight from Nemotron"
}

# Append to log
with open('logs/nemotron_usage.jsonl', 'a') as f:
    f.write(json.dumps(nemotron_log) + '\n')
```

---

## 🎯 SPECIFIC USE CASES

### 1. Trade Performance Audits

**When:** Weekly/monthly reviews, investigating losses

**Data to send:**
- Aggregate metrics (not individual trades)
- Win rate, avg PnL, Sharpe ratio
- Category breakdowns
- Time-series summary

**Ask Nemotron:**
- "Identify edge degradation patterns"
- "Compare strategy A vs B performance"
- "Find hidden correlations in losses"

---

### 2. Strategy Validation

**When:** Before deploying new rules/gates

**Data to send:**
- Backtest summary (not full logs)
- Key metrics before/after
- Sample outcomes (winners/losers)

**Ask Nemotron:**
- "Is sample size sufficient for confidence?"
- "Statistical significance of improvement?"
- "Hidden risks in new strategy?"

---

### 3. Root Cause Analysis

**When:** Production issues, mysterious failures

**Data to send:**
- Error patterns (counts, not logs)
- Timeline of events
- System state snapshots

**Ask Nemotron:**
- "Most likely root cause given these symptoms?"
- "What to check next?"
- "Similar patterns in past incidents?"

---

### 4. Decision Under Uncertainty

**When:** Multiple options, complex trade-offs

**Data to send:**
- Options with pros/cons
- Risk/reward estimates
- Constraints/requirements

**Ask Nemotron:**
- "Rank options by expected value"
- "Worst-case scenario for each option"
- "Decision tree recommendation"

---

## ⚡ QUICK REFERENCE

### Command Template

```bash
timeout 90 python3 ~/.hermes/profiles/comboane-media/scripts/analyze_with_nemotron.py \
  "Brief analysis task (1-2 sentences)" \
  '/tmp/data.json' \
  2>&1
```

### Data Prep Template

```python
data = {
    "problem": "One-line description",
    "key_facts": {"fact1": "...", "fact2": "..."},
    "options": {"A": "...", "B": "..."},
    "question": "Specific question ending with ?"
}
```

### Parse Template

```python
result = terminal(nemotron_command)
try:
    analysis = json.loads(result['output'])
    insight = analysis.get('analysis', '')
    summary = analysis.get('summary', '')
    recommendations = analysis.get('recommendations', [])
except:
    # Fallback: continue without Nemotron
    pass
```

---

## 🎓 LESSONS LEARNED (Today)

### What Worked ✅

1. **Compact data** — 14 signals summarized to <1KB
2. **Specific question** — "Act now or wait?" clear focus
3. **Timeout handling** — 90s sufficient, 60s too short
4. **Statistical validation** — Nemotron calculated p-value, t-score

### What Didn't Work ❌

1. **Large payloads** — First attempt timed out (5KB data)
2. **Vague questions** — "Analyze this" → generic response
3. **Real-time needs** — 90s latency too slow for live monitoring

### Best Practices 💡

1. **Prepare data first** — Clean, aggregate, summarize
2. **Ask specific questions** — "Which option?" not "What do you think?"
3. **Set expectations** — Nemotron is slow but thorough
4. **Always have fallback** — Don't block on Nemotron response

---

## 📋 NEXT TIME CHECKLIST

When you ask me to analyze something complex:

1. **I'll check:** "Is this >2,000 tokens of reasoning?"
2. **If yes:** "Let me offload this to Nemotron 550B"
3. **Prepare:** Compact data summary (<5KB)
4. **Call:** Nemotron with specific question
5. **Parse:** Extract key insights
6. **Act:** Proceed with recommendation

**Your part:**
- ✅ Just ask for analysis as normal
- ✅ I'll automatically use Nemotron when appropriate
- ✅ You'll see "Analyzing with Nemotron 550B..." message

---

## 🎯 COMMITMENT

**Going forward, I will:**

1. ✅ Use Nemotron for heavy analysis tasks (>2K tokens)
2. ✅ Keep main agent lean and action-focused
3. ✅ Validate Nemotron availability before relying on it
4. ✅ Always have fallback reasoning path
5. ✅ Track token savings for ROI

**You can expect:**

- 🚀 Faster main agent responses (less reasoning overhead)
- 💰 Lower token costs (85% savings on analysis)
- 🧠 Deeper insights (550B model reasoning)
- ✅ Same or better quality decisions

---

## 📞 FALLBACK PLAN

**If Nemotron unavailable:**

1. Timeout after 90s → proceed without
2. Error response → use main agent reasoning
3. Service down → continue with local analysis

**Never block workflow on Nemotron.**

---

**Status:** ✅ Strategy documented  
**Next:** Auto-apply in future sessions  
**Benefit:** 85% token savings on analysis tasks

**Setuju dengan approach ini?** 🚀
