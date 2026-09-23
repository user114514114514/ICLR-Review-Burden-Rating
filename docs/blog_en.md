# When Submission Volume Becomes Review Burden: An Open Attempt to Quantify Conference Review Load

In recent years, top machine learning conferences have experienced unprecedented surges in submission volume. Taking [ICLR](https://iclr.cc/) as a prime example, the 2026 cycle received nearly 20,000 valid submissions, generating more than 76,000 official peer reviews; for the subsequent 2027 cycle, paper registrations on [OpenReview](https://openreview.net/) even exceeded 62,000.

Peer review is not an automated industrial pipeline; it relies entirely on the finite, non-renewable time and cognitive effort of fellow researchers. Across online discussions in the research community, one observation has resonated widely: the factor truly straining the peer review system is not merely that more people are doing research, but that within a single deadline, certain accounts or groups batch-submit multiple unpolished, unvetted low-quality manuscripts. This "lottery" approach—banking on reviewer variance to push lucky papers over the acceptance line—privatizes the speculative upside for a handful of individuals while imposing a massive, uncompensated review burden on the entire academic community.

In recent years, with the rapid rise and widespread adoption of generative AI and Large Language Models (LLMs), the barrier to mass-producing papers has collapsed, aggravating this trend even further. We want to state our position unambiguously: we do not oppose using AI to assist research. On the contrary, we believe the efficiency gains and lowered barriers enabled by AI are genuinely positive developments for science. They empower researchers—especially junior scholars and interdisciplinary teams previously constrained by coding bandwidth, computing budgets, or non-native academic English—to test and realize creative ideas at far lower friction and cost. What we oppose is bypassing substantive human thinking, using LLMs to batch-generate and submit unvetted, low-quality papers that offer no meaningful scientific contribution. This is also one of the central reasons our analysis focuses strictly on 2024 and beyond: it was during this period that automated, LLM-facilitated bulk submission escalated into an undeniable systemic issue.

In this context, why did we choose [ICLR](https://iclr.cc/) data? Because ICLR (powered by [OpenReview](https://openreview.net/)) preserves and publicly exposes its entire historical submission record. Unlike the vast majority of traditional academic conferences and journals that publish only accepted papers while locking rejected and withdrawn manuscripts in a proprietary black box, ICLR maintains a fully auditable public ledger: all submissions—accepted, rejected, and withdrawn—along with official peer reviews and discussion threads, remain accessible to the community. This radical transparency makes submission behaviors truly traceable, making our empirical and objective quantification of review burden possible. Here, we also sincerely call upon more premier academic conferences and leading journals to adopt and promote this fully transparent model. Only when sunlight illuminates the entire peer review cycle can the health of our shared research commons be safeguarded over the long term.

Recognizing this shared challenge across the community, we undertook an open attempt: building a transparent, open-source, auditable metric—the ICLR Review-Burden Score—based on public [OpenReview](https://openreview.net/) records to objectively characterize this specific pattern of review burden. We are not conference organizers, and this work does not seek to pass moral judgment on any researcher. It is an open data initiative aimed at providing the community with an objective lens to better understand and discuss the peer review ecosystem.

---

## Scope and Guardrails: What We Measure (and What We Deliberately Avoid)

From the outset, our primary concern was preventing metric misuse. We established strict, deliberate boundaries:

1. This is not an assessment of scientific merit, researcher ethics, or intent. The algorithm reads only public metadata: ratings, confidence scores, final decisions, and author positions. It cannot evaluate subtle theoretical novelty, understand niche subfield controversies, or know if an exploratory submission simply encountered hurried reviewers. A high score indicates only that a public submission record exhibits a statistically dense concentration of low scores; it does not infer deliberate or malicious intent.
2. Author position is a coarse proxy for responsibility. In modern research, collaboration structures and authorship conventions vary dramatically. While our model sharply down-weights middle and senior author positions, no mathematical formula can perfectly reconstruct who actually ran the experiments or drafted the manuscript.
3. Scores in the low range have zero comparative meaning. Whether a score is 0, 2, or 4, minor numerical differences in this band are pure statistical noise and must never be interpreted as "one researcher doing worse work than another." Empirical calculations show that under the Peak Score metric, 99% of all authors score at or below roughly 5. For scores below 5, variations are heavily influenced by natural research exploration, collaborative volume, and reviewer subjectivity, possessing virtually zero evaluative value. This metric is never intended to rank or categorize ordinary researchers; its focus rests strictly on anomalous, densely clustered, and visibly low-quality submissions that impose undeniable community strain within a single cycle.
4. It is an index, not a verdict. The score acts merely as an audit pointer: *"This public record exhibits an unusual pattern that warrants closer, human inspection."* The only objective evidence remains the underlying manuscripts, reviews, and author rebuttals.

---

## Why Counting Submissions or Rejection Rates Fails

In discussions of conference review load, common proposals include "capping submissions per author" or "penalizing high rejection rates." An examination of the data reveals critical flaws in both approaches:

- Simply counting submissions penalizes productive, high-quality research. Consider a researcher who coauthors 15 submissions in a single year: 10 are accepted, 2 are borderline rejections that sparked valuable debate, and 3 missed the mark. While these 15 submissions consumed review bandwidth, they represent serious, competitive contributions. Labeling them as a "problematic burden" would be fundamentally unfair.
- Using rejection rates punishes honest scientific exploration. In competitive venues where acceptance rates hover around 25%, failing to get in is the statistical norm. An author who submits two papers and sees both rejected with respectable scores of 5.5 has participated in good faith. That is normal scientific dialogue, not wasted reviewer effort.

Our core design principle is therefore:

> Burden is not about rejection. It is about how far a paper falls below a reasonable quality floor for that year, and whether that rock-bottom quality is repeatedly submitted within the same cycle.

---

## Formulation: From Individual Reviews to Annual Totals

To maintain interpretable physical meaning and guard against rogue outliers, we designed the score in modular steps:

### 1. Robust Paper Score ($R_p$)

For a paper evaluated by reviewers with ratings $r_i \in [1, 10]$ and confidence scores $c_i \in [1, 5]$:

First, reviewer confidence should inform the consensus, but no overconfident reviewer should single-handedly dictate the outcome. We apply a gentle linear scale:

$$
q_i = 0.6 + 0.1 c_i.
$$

Confidence 1 receives weight $0.7$, while confidence 5 receives $1.1$ (modulating influence by roughly $\pm 25\%$).

Second, to protect against rogue or malicious scores (e.g., an unjustified 1 or 10), we compute the median rating $m_p = \mathrm{median}(r_1, \ldots, r_n)$ and clip all ratings to within $\pm 2$ points of that median:

$$
\tilde r_i = m_p + \mathrm{clip}(r_i - m_p, -2, 2).
$$

The Robust Review Score $R_p$ is then the confidence-weighted mean:

$$
R_p = \frac{\sum_i q_i \tilde r_i}{\sum_i q_i}.
$$

---

### 2. Adaptive Benchmarks: Reasonable Submission Floor ($T_y$) and High Bar ($H_y$)

Grading scales shift across conference editions; an absolute cutoff (such as 4.0 or 5.0) would be arbitrary. Instead, we calibrate each year against that edition's accepted papers.

Let $Q_{25,y}^{acc}$ and $Q_{75,y}^{acc}$ denote the 25th and 75th percentiles of accepted-paper scores for year $y$. We estimate the dispersion scale parameter as:

$$
s_y = \max\left(\frac{Q_{75,y}^{acc} - Q_{25,y}^{acc}}{1.349},\, 0.5\right).
$$

From this, we construct the Reasonable Submission Floor:

$$
T_y = Q_{25,y}^{acc} - s_y.
$$

This floor is generous: it sits a full standard deviation below the lower quartile of papers that actually got in. Any rejected or withdrawn paper scoring above $T_y$ is treated as an ordinary, honorable rejection—incurring zero deficit ($p_p = 0$).

Only papers that fall below $T_y$ accumulate a quality deficit:

$$
p_p = \left[\frac{T_y - R_p}{s_y}\right]_+.
$$

Accepted papers are never penalized, regardless of numerical score ($p_p = 0$).

At the top end, we set a high bar at the third quartile of accepted work, $H_y = Q_{75,y}^{acc}$. Accepted papers exceeding this threshold receive bounded credit:

$$
g_p = 3 \tanh\left[\frac{1}{2}\left(\frac{R_p - H_y}{s_y}\right)_+\right].
$$

The hyperbolic tangent strictly caps credit at 3.0, preventing multiple collaborative acceptances from serving as an unlimited license to flood the venue with unvetted drafts.

---

### 3. Authorship Weights, Saturating Single-Paper Severity, and Compounding Burden ($B_{a,y}$)

Penalty weights decay with author position $k$:

$$
\alpha_k =
\begin{cases}
1.0, & k = 1,\\
0.3, & 2 \le k \le 4,\\
0.1, & k \ge 5.
\end{cases}
$$

Credit weights $\beta_k$ follow a similar decay ($1.0, 0.2, 0.05$).

When evaluating how severely an individual sub-floor paper burdens reviewers, extreme deficits must count, but a single paper's deficit should not scale without limit. We introduce a bounded, saturating single-paper severity function:

$$
h(p) = \frac{p}{1 + 2p}.
$$

This function satisfies $h(0) = 0$, $h(0.5) = 0.25$, $h(1) \approx 0.333$, $h(2) = 0.4$, and asymptotically approaches $0.5$ as $p \to \infty$. The author rank coefficient $\alpha_k$ operates strictly outside the severity transform, giving each paper a factor of $1 + \alpha_k h(p)$. Consequently, the maximum multiplicative factor per paper is naturally bounded: at most 1.5 for a first author ($\alpha = 1.0$), 1.15 for ranks 2–4 ($\alpha = 0.3$), and 1.05 for rank 5+ ($\alpha = 0.1$).

For author $a$ in year $y$, all reviewed low-quality papers with positive deficit ($p_p > 0$) combine into the Compounded Burden Term:

$$
B_{a,y} = 4 \left[ \prod_{p \in L_{a,y}} \left(1 + \alpha_{k(a,p)} \, \frac{p_p}{1 + 2p_p}\right) - 1 \right].
$$

Why a product with saturation?
An occasional weak paper is an ordinary risk of research; a linear sum would excessively penalize occasional stumbles. Under a product formulation, a single weak paper incurs a small penalty. Crucially, the saturating severity function ensures that severe single-paper deficits still count, but their marginal effect saturates: repeated low-quality submissions within the same cycle decisively dominate a small number of isolated, rock-bottom papers.

Positive credit $G_{a,y}$ provides a capped linear offset:

$$
G_{a,y} = \min\left(3.0, \, \sum_{p} \beta_{k(a,p)} \, g_p\right).
$$

---

### 4. Desk Rejects and Portfolio Deficit Ratio ($\rho_y$)

Desk rejects (formatting, anonymity, or page-limit violations) typically do not exhaust multiple full review cycles, but repeated desk rejects in the same cycle demonstrate negligence. We assign them a small, separate linear term:

$$
D_{a,y} = 0.1 \sum_{d \in DR_{a,y}} \alpha_{k(a,d)}.
$$

We also compute the ratio of problematic submissions to total decided papers ($N_{\text{bad}} = N_{\text{low}} + N_{\text{DR}}$):

$$
\rho_y = \frac{N_{\text{bad}}}{N_{\text{bad}} + N_{\text{acc}}}.
$$

This cleanly separates someone with 3 problematic papers out of 4 submissions ($\rho = 0.75$) from a productive group with 3 problematic papers alongside 10 accepted papers ($\rho = 0.23$).

---

### 5. Independent Annual Scores and Peak Score Ranking

Combining all modules, the author's Annual Score for year $y$ is:

$$
\boxed{
A_{a,y} = (1 + \rho_y) \max(0, B_{a,y} - G_{a,y}) + N_{\text{bad}, a, y} \, D_{a,y}
}
$$

We adhere to an essential design rule: each year stands completely on its own, with no cross-year accumulation.

Accumulating scores over time effectively penalizes academic longevity: an author active in the community for a decade would automatically carry a higher score than a newcomer, even if their recent submissions were entirely responsible. Science is iterative; each conference edition should start with a clean slate.

When a cross-year ranking is needed, the system reports the author's Peak Score:

$$
\text{Peak Score}_a = \max_{y} A_{a,y}.
$$

The Peak Score asks a clear, bounded question: *What was this author's single most burdensome cycle on record, and how intense was it?* It isolates peak bulk-dumping episodes without penalizing the length of a career.

---

## Data Discipline: Strict Identity Binding

This project operates on the public [qhjqhj00/iclr-openreview-reviews](https://github.com/qhjqhj00/iclr-openreview-reviews) dataset, with scoring strictly confined to [ICLR](https://iclr.cc/) 2024 through 2026 (38,890 submissions), where data schemas are most consistent and submission surges have been most acute.

On author identity, we enforce strict conservatism: an author position is scored if and only if it links cleanly to a canonical [OpenReview](https://openreview.net/) Profile ID (`~Given_Family1`). We do not perform fuzzy name matching, email domain clustering, or graph-based inferences. Unresolved author positions remain unresolved; the underlying papers still contribute to global conference calibrations, but are never attributed to individual author profiles. While this leaves roughly 4.7% of author positions unresolved in the current snapshot, we believe this is the right ethical boundary: mistakenly associating a low-quality paper with an innocent researcher is far worse than temporarily omitting an unresolved one.

---

## Conclusion

Across all 93,515 authors analyzed in ICLR 2024–2026, the empirical distribution reveals a clear reality: under the Peak Score metric, 99% of authors score at or below roughly 5 (median around 0.35, 75% below 1.02, and 90% below 2.93). We reiterate emphatically: scores below 5 are subject to natural exploratory variance, collaboration footprints, and reviewer subjectivity, carrying virtually zero evaluative value. The vast majority of researchers submit thoughtfully, and normal exploratory misses, early-career learning curves, and occasional reviewer bad luck fall comfortably within this range—which the peer review commons can and should readily accommodate. The strain on peer review is driven solely by an extreme tail of around 1%. Our focus rests strictly on anomalous, densely clustered, and visibly low-quality submissions within a single cycle.

An open-source metric cannot fix complex academic publishing incentives on its own. However, public, transparent data is the prerequisite for grounded discussion. We hope this attempt provides the community with an objective, auditable window into public review records, helping support more constructive conversations on preserving our shared peer review commons.
