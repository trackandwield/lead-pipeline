# Scoring spec: Lumenvale (fictional)

Status: **draft for review**, 2026-11-19. The numbers are starting guesses for a product marketer to review, not benchmarks. The machine-readable copy is [`../scoring.yaml`](../scoring.yaml). If the two disagree, the YAML wins.

## The seller

**Lumenvale** is a made-up observability SaaS: logs, traces and metrics in one place, built on OpenTelemetry, priced per GB ingested. It sells to mid-market engineering teams who have outgrown open-source tools or find their current vendor's bill too high.

All leads in this project are synthetic. No real company or person is used.

## Ideal customer profile

| Attribute | Best fit | Still good | Poor fit |
| --- | --- | --- | --- |
| Employees | 200 to 2,000 | 50 to 199, or 2,001 to 10,000 | Under 50, or over 10,000 (long procurement) |
| Industry | B2B SaaS, fintech, e-commerce, healthtech | Media, logistics, gaming, edtech | Everything else |
| Stack | Kubernetes, OpenTelemetry, AWS or GCP | A competitor tool in place (a displacement deal) | No cloud footprint |

**Buyer roles**

| Role | Examples | Points |
| --- | --- | --- |
| Economic buyer | VP Engineering, CTO, Head of Platform | 15 |
| Champion | SRE lead, platform or DevOps manager | 12 |
| User | SRE, backend or platform engineer | 6 |
| Unrelated | Marketing, HR, sales, students | 0 |

The LLM makes the role call from the job title and company. Rules handle everything else, because those signals are objective.

## Score (0 to 100)

**Fit, up to 60:** employees (up to 20) + industry (up to 10) + stack signals (up to 15) + role (up to 15).

**Intent, up to 40, from the last 30 days:**

| Signal | Points |
| --- | --- |
| Demo request | 20 |
| Pricing contact or trial signup | 15 |
| Trial sent data | 10 |
| Two or more pricing page visits | 8 |
| Three or more docs visits | 6 |
| Webinar | 6 |
| Ebook | 4 |
| Newsletter | 1 |

**Penalties and disqualifiers:** a personal email domain costs 10 points, and a student or job seeker costs 30. A competitor's domain, or a company under 10 employees, is disqualified outright.

## Tiers and routing

| Tier | Score | Routing |
| --- | --- | --- |
| Hot | 70 and up | MQL. Slack alert with a brief, then handoff to Salesforce |
| Warm | 45 to 69 | MQL. Email sequence, then handoff to Salesforce |
| Nurture | Under 45 | Nurture list. Not handed to sales |

## Setpoints: what the loop steers toward

The closed loop measures **MQL-to-SQL conversion** per tier over a rolling 4 weeks. SQL means the SDR accepted the lead in Salesforce.

| Tier | Target band | If below the band | If above the band |
| --- | --- | --- | --- |
| Hot | 45% to 65% | Raise the Hot threshold (too many weak leads are getting in) | Lower it (good leads are being kept out) |
| Warm | 15% to 30% | Raise the Warm threshold | Lower it |
| Nurture | Under 3% later become SQL | Not used | Lower the Warm threshold (good leads are being missed) |

**Guardrails**

- Move each threshold at most 3 points a week.
- Make no change for a tier with fewer than 30 MQLs in the window.
- Hot stays between 60 and 85, Warm between 35 and 60, and Hot stays at least 10 above Warm.
- **Approval mode** posts each proposed change to Slack with Approve and Reject buttons. **Autonomous mode** applies the change inside these guardrails and only notifies. The demo shows both.
- The slow loop (monthly, from won and lost results) can only *suggest* weight changes of up to 20%. A person always approves those.

## Open questions for PMM review

1. Does the ICP ring true for an observability product like this? What would change?
2. Are the role points right? For example, should a champion be worth as much as an economic buyer?
3. Are the intent weights right? Is a webinar really worth more than an ebook?
4. Are the target bands believable? What MQL-to-SQL rates are typical for hot and warm leads?
5. Is anything missing that would be scored in real life, such as job changes, funding news or hiring for SRE roles?
