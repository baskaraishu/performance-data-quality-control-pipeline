# Business Problem and Project Scope

## Project Purpose

This portfolio project demonstrates how an analyst can check operational performance data before using it to support management decisions. The pipeline will identify data-quality problems, show which measures are suitable for analysis, and present exceptions for human review.

All records, establishment names, measures, and results are synthetic. They are not MoJ or HMPPS operational data and must not be presented as real findings.

## Business Problem

Operational managers need timely information to understand performance, compare measures with targets, and decide what requires follow-up. If records are missing, duplicated, invalid, or inconsistent, a reported KPI may give a misleading picture.

The project addresses this question:

> Which monthly performance results can be used with confidence, and which should be checked before managers act on them?

## Intended Users

- A performance analyst preparing and assuring monthly management information.
- An operational manager reviewing trends, target variances, and exceptions.
- A data owner asked to investigate a reported data-quality issue.

## Decisions the Analysis Should Support

1. Identify records and measures that need correction or investigation.
2. Distinguish a data-quality concern from a possible performance concern.
3. Review supported KPI trends and target variances by establishment and reporting month.
4. Decide which exceptions merit follow-up, using the data-quality status as context.

An unusual value is a prompt to investigate, not proof that the value is wrong. The reports support analyst judgement; they do not make operational decisions.

## First-Version Scope

- Use the supplied `performance_dirty.csv` as the intentionally messy raw learning dataset. A separate clean baseline file is not expected.
- Use `establishments.csv` and `kpi_targets.csv` as synthetic reference inputs.
- Intended performance-record grain: one establishment per reporting month.
- Assess completeness, validity, uniqueness, consistency, referential integrity, and timeliness.
- Recalculate occupancy and incident rates when the source values and denominator are valid.
- Show education attendance, purposeful activity, sickness, and resignation percentages only as supplied values unless their source components and definitions are added. Do not claim to independently validate their calculation or aggregate them as if their denominators were known.
- Produce trusted Gold performance/KPI outputs and quality/reconciliation reports for synthetic data.

## Out of Scope for the First Version

- Accessing, reproducing, or implying access to real confidential MoJ/HMPPS data.
- Predicting future performance or using machine learning to rank establishments.
- Claiming that a KPI change was caused by a particular intervention.
- Automatically imputing missing operational facts or silently resolving ambiguous duplicates.
- Production source-system integration, operational alerts, or automated scheduling.

## Success Criteria

- A user can trace each reported quality issue to its source record and rule.
- The pipeline explains how input records become accepted, quarantined, or otherwise excluded.
- Recalculable KPIs match their documented formulas and are marked for review when their supporting data fails checks.
- Reports distinguish supplied measures, derived measures, data issues, and possible performance exceptions.
- A reader can reproduce the project and understand its synthetic-data limitations from the documentation.