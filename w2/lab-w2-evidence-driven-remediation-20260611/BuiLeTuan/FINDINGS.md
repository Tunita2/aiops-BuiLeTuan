# Findings

## 1. Similarity function

I used a weighted hybrid similarity:

```text
similarity =
    0.45 * log cosine similarity
  + 0.30 * trace similarity
  + 0.15 * affected-service Jaccard similarity
  + 0.10 * metric similarity
```

Logs are normalized by replacing volatile values such as numbers, IDs, and IP
addresses. TF-IDF with word unigrams and bigrams then compares the normalized
live logs with historical log signatures. Trace similarity rewards an exact
edge match most strongly, then partial source or target matches, and also
compares anomaly strength. Metric similarity compares metric names, services,
and change direction.

I considered metric-only Euclidean distance, but rejected it because metric
names and scales differ between incidents and the assignment requires genuine
log and trace evidence. The hybrid method retrieved three
`connection_pool_exhaustion` incidents for E01. Its closest neighbor scored
0.6195 overall, composed of log 0.4242, trace 0.9727, service 0.4286, and
metric 0.7250. This is stronger and more interpretable than relying on the
metric score alone.

## 2. Effect of outcome-weighted voting

Historical outcomes use these weights:

| Outcome | Weight |
|---|---:|
| success | 1.00 |
| partial | 0.45 |
| failed | -0.75 |

E03 demonstrates a ranking change among the lower candidates. With pure
similarity voting, `page_oncall` scored 0.2511 and ranked above `restart_pod`
at 0.2494. The page vote came from a `partial` historical incident. After
outcome weighting, its vote fell to `0.2511 * 0.45 = 0.1130`, so
`restart_pod` moved above it.

E01 also shows why weighting matters even when the top action does not change.
Pure similarity gave `rollback_service` 1.5755. One contributing neighbor was
only a partial success, so weighted voting reduced the rollback vote to
1.3126. `increase_pool_size` remained at 1.0975 because both supporting
neighbors were successful. The rollback advantage therefore narrowed from
0.4780 to 0.2151.

The current eight-case corpus does not contain a case where outcome weighting
alone reverses the top-ranked action. I did not claim such a reversal; the
measured effects are the E03 candidate-order change and the reduced influence
of partial outcomes.

## 3. Full utility calculation for E01

E01 retrieved three connection-pool incidents. Outcome-weighted votes were:

| Candidate | Raw vote | Vote share |
|---|---:|---:|
| `rollback_service` | 1.3126 | 0.5446 |
| `increase_pool_size` | 1.0975 | 0.4554 |

The closest-neighbor similarity was 0.6195. Confidence is:

```text
confidence = 0.60 * best_similarity + 0.40 * vote_share
```

This produced:

```text
rollback confidence
= 0.60 * 0.6195 + 0.40 * 0.5446
= 0.5895

increase-pool confidence
= 0.60 * 0.6195 + 0.40 * 0.4554
= 0.5539
```

The utility function is:

```text
utility =
    confidence * 100
  - cost_min * 0.8
  - downtime_min * 2
  - blast_radius_services * 8
```

For rollback, cost is 10 minutes, downtime is 2 minutes, and blast radius is
one service:

```text
rollback utility
= 58.95 - 10*0.8 - 2*2 - 1*8
= 38.95
```

For increasing the pool, cost is 1 minute, downtime is zero, and blast radius
is one service:

```text
increase-pool utility
= 55.39 - 1*0.8 - 0*2 - 1*8
= 46.59
```

Both actions passed the confidence and blast-radius gates. The engine selected
`increase_pool_size` for `payment-svc` because its utility exceeded rollback
by approximately 7.63 points.

## 4. Escalation behavior

The final run escalated six incidents:

| Incident | Best similarity | Reason | Ground-truth result |
|---|---:|---|---|
| E02 | 0.3736 | Paging evidence was within 90% of the best auto-action vote | Correct |
| E04 | 0.2188 | Below the 0.25 OOD threshold | Correct |
| E05 | 0.5463 | Candidate confidence remained below the 0.48 auto-action threshold | Correct |
| E06 | 0.6239 | Historical payment actions conflicted with dominant trace `cart-svc -> cart-redis` | Correct |
| E07 | 0.4593 | Historical paging vote dominated/competed with auto-action evidence | Correct |
| E08 | 0.2684 | Candidate confidence was insufficient; this avoided acting on `bb-edge` | Correct |

E03 was not escalated. Its best similarity was only 0.2551, but the current
root `esb` appeared on the dominant trace edge and `restart_pod` had direct
historical support of 0.2494. A constrained transfer rule raised confidence
to 0.50 and applied the action to `esb`. E01 was also auto-acted. The supplied
evaluation set therefore finished at 8/8 accepted actions, with zero forbidden
actions and zero missing audit entries.

## 5. Most likely failure mode

The most likely failure is a novel multi-service cascade where the noisiest
log service and the dominant trace edge are both downstream symptoms rather
than the root cause. The current service ranking is heuristic. For example,
E01 initially ranks `checkout-svc` as the suspected root even though the
selected action correctly targets `payment-svc` using historical parameters
and trace consistency. E08 similarly ranks `bb-edge`, while the incident notes
identify the deeper `t24-service` as the true root.

A concrete improvement would be temporal causal propagation over the topology:
detect the first anomaly timestamp per service, traverse dependency edges, and
prefer the deepest service whose anomaly precedes its callers. This would
replace the current static weighted service score with an explicit causal
graph. I did not implement it because the evaluation files have inconsistent
declared metric windows and because validating temporal thresholds with only
29 historical incidents would likely overfit. The current implementation uses
trace-consistency and confidence gates to escalate instead of taking an unsafe
action when root localization is uncertain.

## POC and reproducibility summary

The final `audit.jsonl` contains exactly eight entries. The grader result is:

```text
Correct: 8/8
Forbidden: 0/8
Missing from audit: 0/8
```
