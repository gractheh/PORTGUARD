# aicord — Product Vision

## Problem

Two domains that used to be separate are increasingly the same job. Software engineers build the platforms that move goods across borders — ACE/AES integrations, denied-party screening systems, trade data pipelines, customs automation tools, HS classification engines. Trade compliance professionals now live inside these systems, not just beside them. But expert knowledge in both domains rarely sits in the same person, and almost never in the same tool.

The result: engineers guessing at tariff classification logic. Compliance officers unable to evaluate the technical decisions shaping their workflows. Questions that require both lenses — "how should we model FTZ inventory in our WMS?" or "is this ACE filing architecture compliant with CBP's reconciliation requirements?" — that have no good place to go.

## Solution

**aicord** is an AI expert assistant with genuine mastery in software engineering and international trade compliance. Not a general-purpose chatbot with a customs FAQ bolted on — a system that reasons at the level of a principal engineer and a senior CBP official simultaneously, and synthesizes across both domains without prompting.

It answers the question that actually needs answering, cites the authority that governs it, and tells you when something has changed.

## Target Users

**Primary**
- Software engineers building trade and logistics platforms (customs brokers, freight forwarders, importers, 3PLs, ERP vendors)
- Trade compliance managers and analysts who work closely with technical teams
- Engineers and architects at companies with significant import/export volume

**Secondary**
- Consultants advising on trade compliance program design or systems modernization
- Product managers scoping customs automation features
- Startups building in the trade-tech space who need expert input without expert headcount

## Core Use Cases

1. **Classification & duty analysis** — "What's the correct HTSUS classification for this product, and what duties apply given current Section 301 status?"
2. **Compliance architecture review** — "We're building a denied-party screening service — what data sources, cadence, and match logic does a defensible program require?"
3. **Cross-domain design** — "How should we model rules-of-origin tracking in our bill-of-materials system to support USMCA certification at entry?"
4. **Regulatory guidance** — "Our ACE filing is getting rejected on this field — what does the CBP spec require here?"
5. **Risk analysis** — "We're sourcing a component from a new supplier in this country — what export control, sanctions, and AD/CVD exposure do we have?"
6. **Code review with trade context** — "Review this tariff classification service and flag both engineering and compliance issues."

## What Makes It Different

- **Dual-domain depth, not breadth** — The system prompt is written to produce expert-level answers in both domains, not surface-level coverage of many domains.
- **Cross-domain synthesis by default** — When a question touches both domains, both lenses are applied without asking.
- **Regulatory precision** — Answers cite specific statutes, HTSUS subheadings, CFR provisions, and license exceptions. Not paraphrases — authorities.
- **Volatility awareness** — The assistant flags when an answer depends on a regulation that changes frequently (AD/CVD rates, Section 301 exclusions, entity list entries) and directs users to authoritative sources.
- **Adaptive reasoning** — Uses Claude's adaptive thinking capability, so complex multi-factor questions (classification disputes, sanctions risk analysis, system architecture reviews) receive deep, structured analysis rather than surface responses.

## Success Criteria

- A software engineer can get a correct HTSUS classification, with GRI analysis and applicable duty rates, in a single exchange.
- A compliance professional can describe a business scenario and receive a structured risk analysis that cites controlling regulations.
- A cross-domain question (e.g., designing a customs automation platform) produces an answer that addresses both the engineering and compliance dimensions without the user having to ask twice.
- Responses are direct: the answer comes first, the reasoning follows, and disclaimers appear only when they are genuinely warranted.

## Non-Goals

- General-purpose assistant (weather, recipes, creative writing)
- Replacing licensed customs brokers or trade attorneys for binding legal determinations
- Real-time regulatory data feeds (the assistant reasons about law; it does not scrape live databases)
- Multi-user SaaS product (current scope: single-user CLI)
