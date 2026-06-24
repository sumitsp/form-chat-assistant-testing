# Loan Details — Contextual Field Labels Spec

> Drop-in spec for the form and chat intake layers. Defines the contextual label, visibility, and required/computed status for every Loan Details field across the six core scenarios. Also enumerates new conditional fields to add and existing fields to collapse or remove.

## Scope

Applies to the Loan Details group (form mode) and the equivalent chat-mode slots after `occupancy`, `primaryLoanPurpose`, `lienPosition`, and `propertyType` are captured. The triangle (`valueSalesPrice`, `loanAmount`, `ltv`) is "any two of three → compute the third" in both modes; CLTV is always computed (never asked directly).

## Legend

- ✓ required
- ◯ computed / display only
- ◐ conditional (shown only when a prior answer triggers it)
- — hidden / not shown

## Master matrix — labels and visibility per scenario

| Field | Purchase + 1st | Purchase + Piggyback | R&T Refi + 1st | R&T Refi + Standalone 2nd | Cash-Out + 1st | Cash-Out + Standalone 2nd |
|---|---|---|---|---|---|---|
| **Property Value** | `Sales Price` ✓ | `Sales Price` ✓ | `Appraised Value` ✓ | `Appraised Value` ✓ | `Appraised Value` ✓ | `Appraised Value` ✓ |
| **Loan Amount** | `Loan Amount` ✓ | `New Piggyback (2nd) Amount` ✓ | `New Loan Amount` ✓ | `New 2nd Lien Amount` ✓ | `New Loan Amount` ✓ | `New 2nd Lien Amount` ✓ |
| **LTV** | `LTV` ✓ | `LTV on 2nd` ✓ | `LTV` ✓ | `LTV on 2nd` ✓ | `LTV` ✓ | `LTV on 2nd` ✓ |
| **CLTV** | — | `CLTV ((1st + 2nd) ÷ value)` ◯ | `CLTV` ◐ *(only when subordinating 2nd)* | `CLTV ((1st + 2nd) ÷ value)` ◯ | `CLTV` ◐ *(only when subordinating 2nd)* | `CLTV ((1st + 2nd) ÷ value)` ◯ |
| **Down Payment** | `Down Payment` ◯ | `Down Payment` ◯ | — | — | — | — |
| **Existing 1st Lien Balance** | — | `New First Mortgage Amount` ✓ | `Existing 1st Lien Balance` ✓ | `Existing 1st Lien Balance` ✓ | `Existing 1st Lien Balance` ✓ | `Existing 1st Lien Balance` ✓ |
| **Existing 2nd Lien (Y/N)** | — | — | `Existing 2nd lien on title?` ✓ | — | `Existing 2nd lien on title?` ✓ | — |
| **Existing 2nd Lien Balance** | — | — | ◐ *(only if "Yes — needs subordination")* | — | ◐ *(only if "Yes — needs subordination")* | — |
| **2nd Lien Product** | — | — | — | `HELOC or HELOAN?` ✓ *(ask before Loan Details)* | — | `HELOC or HELOAN?` ✓ *(ask before Loan Details)* |
| **HELOC Draw Period** | — | — | — | `Draw Period` ◐ *(2 / 3 / 5 yr, only if HELOC)* | — | `Draw Period` ◐ *(2 / 3 / 5 yr, only if HELOC)* |
| **Cash-Out Amount Requested** | — | — | — | — | `Cash-Out Request` ✓ | `Cash-Out Request` ✓ |

## Label rules

1. **Property Value relabels by purpose** — `Sales Price` on Purchase, `Appraised Value` on Refi. Same slot, same validation, different chrome.
2. **Loan Amount relabels by lien position** — `Loan Amount` for first-lien scenarios, `New Piggyback (2nd) Amount` or `New 2nd Lien Amount` whenever the lien is a second. Engine math stays uniform (`LTV = loanAmount / value`), but the LO sees a label that matches what they're entering.
3. **CLTV is always computed and never asked directly.** Display only.
4. **Down Payment only renders on Purchase scenarios.** Hidden on all refi.

## Additional fields to add

| New field | When asked | Why |
|---|---|---|
| `helocDrawYears` | Conditional on `secondLienProduct == heloc` | LoanPASS has 12 HELOC products split by draw (2 / 3 / 5 yr). Needed for product-level pricing. Options: `2`, `3`, `5`. |
| Split `cashInHandRequest` → `helocMaxLine` + `helocInitialDraw` | When `secondLienProduct == heloc` AND scenario is Cash-Out + 2nd | "Cash out" is ambiguous on a HELOC — full line versus initial draw. LoanPASS prices off both. |
| `existingLiensByProperty[]` | When `propertyType == multiple_properties` (cross-collateral) | Combined totals alone aren't enough; eligibility needs per-property existing liens to compute combined CLTV correctly. |
| `subjectStatesByProperty[]` | When `propertyType == multiple_properties` | If properties span 2+ states, the state-level eligibility check has to fire per property, not on a single state field. |

## Fields to remove / collapse

| Remove or collapse | Why |
|---|---|
| `Existing 2nd Lien (Y/N)` on **Refi + Standalone 2nd** scenarios | The standalone-2nd-refi flow implies there's an existing 1st. The Y/N gate is meaningless — collect the 1st balance directly. |
| `Existing 1st Lien Balance` marked as "optional" on R&T + 1st | Make it required. Without it, cash-out classification and CLTV-on-subordination both break. |

## Implementation notes for the form layer

- Render the matrix's "scenario column" by computing `(loanPurpose, lienPosition)` once at the top of the Loan Details step and using it to select the field-label set.
- The CLTV row's ◐ behaviour on R&T + 1st and Cash-Out + 1st is gated by the `Existing 2nd Lien (Y/N)` answer being `"Yes — needs subordination"`.
- The piggyback `Existing 1st Lien Balance` field is relabelled to `New First Mortgage Amount` and stays in the same underlying slot.
- Down Payment value: Purchase + 1st = `value − loanAmount`; Purchase + Piggyback = `value − existingFirstLien − loanAmount`.

## Implementation notes for the chat layer

- Slot order: `propertyValue` → `loanAmount` → `ltv` (any two of three → derive the third). For piggyback / standalone 2nd, also collect `existingFirstLien` before LTV is computed.
- Allowed pairs for the planner to combine in a single turn:
  - `(propertyValue, loanAmount)`
  - `(loanAmount, ltv)`
  - `(propertyValue, ltv)`
  - `(existingFirstLien, loanAmount)` *(piggyback / standalone 2nd only)*
- The `secondLienProduct` slot must be asked **before** the Loan Details triangle when `lienPosition == second_lien` (standalone), so the chat knows whether to follow up with `helocDrawYears`.
- The `cltv` slot is `kind: "computed"` — the planner never selects it; the eligibility layer reads it from `existingLien + loanAmount + propertyValue`.

## What this fixes

1. Loan officers stop entering the new first lien into the `Loan Amount` field on a piggyback scenario — the label tells them exactly what to type.
2. Eligibility math becomes correct on R&T + 1st refis where the existing balance was previously "optional".
3. HELOC pricing now lands on the right LoanPASS product because draw period is captured.
4. Cross-collateral scenarios get the per-property data they need for combined CLTV and per-property state eligibility.
