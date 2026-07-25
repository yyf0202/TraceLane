# Evidence Registry

The Evidence Registry is the checked-in, content-addressed record of candidate
evidence selected for a research project. It is separate from both fixtures and
run artifacts:

- **Evidence** holds source locators, licensed or paraphrased content blobs,
  candidate records, project indexes, and any retained human reviews.
- **Fixtures** are benchmark inputs and expected outputs. A fixture can refer to
  approved evidence, but evidence candidates do not become fixtures by being
  recorded here.
- **Artifacts** are local outputs from acquisition and TraceLane runs. They are
  not checked in as evidence merely because they were used during import.

## Status and review

Candidates begin as `pending`. A bound human review may later make the
effective status `approved` or `rejected`; `superseded` records a replacement
without erasing the earlier decision. Reviews are retained with the project so
that an approval remains bound to the reviewed candidate record, content,
locator, and licensing decision. No candidate is available for a curated
historical fixture until a human explicitly approves it.

The registry stores repository-authored paraphrases unless a candidate's
retention policy and license basis permit more. Source URLs, license bases, and
retention policies remain part of each candidate record; importing a candidate
does not grant a license for additional text or images.

## Commands

Prepare an operator-controlled candidate package, then import it into a local
registry root. The source location stays an operator argument and is not
persisted in the registry.

```powershell
.\.venv\Scripts\python.exe scripts\prepare_hist001_candidates.py --artifact-root $env:TRACELANE_HIST001_SOURCE
.\.venv\Scripts\python.exe scripts\import_hist001_evidence.py --source $env:TRACELANE_HIST001_SOURCE --target evidence
.\.venv\Scripts\tracelane.exe evidence verify --root evidence --project hist-001
.\.venv\Scripts\tracelane.exe evidence list --root evidence --project hist-001 --status pending
```

Re-running the import is an identity check: it succeeds only when the existing
canonical records and blobs match the authenticated candidate package. Human
approval remains a separate, deliberate gate; neither preparation nor import
creates review JSON or publishes a fixture.
