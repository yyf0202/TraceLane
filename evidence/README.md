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

`append_review(root, review)` is the review mutation boundary. It holds the
shared registry lock, authenticates the project and candidate, requires the
review to extend the current head exactly once, and publishes an immutable
review record. An approval's effective fact and domain scope comes from that
review; the candidate's proposed scope remains in the candidate record for
audit. Appending a review intentionally leaves the derived project index and
global registry stale until they are rebuilt.

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
.\.venv\Scripts\tracelane.exe evidence rebuild-index --root evidence --project hist-001
.\.venv\Scripts\tracelane.exe evidence verify --root evidence --project hist-001
.\.venv\Scripts\tracelane.exe evidence list --root evidence --project hist-001 --status pending
.\.venv\Scripts\tracelane.exe evidence find --root evidence --project hist-001 --status approved --domain diplomacy --clean
```

Re-running the import is an identity check: it succeeds only when the existing
canonical records and blobs match the authenticated candidate package. Human
approval remains a separate, deliberate gate; neither preparation nor import
creates review JSON or publishes a fixture.

The import commit point is the successful authenticated registry verification
and construction of its report. Once reached, staging cleanup or output errors
cannot turn the committed import into an ordinary reported failure. Before that
point, a newly published project directory and registry replacement are held by
ownership receipts and rolled back together on failure. Importer-owned project
and staging directories are never recursively deleted by the online workflow.
They are atomically retired under the target's sibling
`.tracelane-staging/retired/` namespace with randomized names. That namespace is
outside evidence discovery and verification, so retained residue is not part of
the Evidence Registry. Removing retired residue is an explicit offline
maintenance action after an operator has independently confirmed ownership; the
import API does not clean it automatically.

After a review append, `rebuild-index` derives every project's index from
source records and replaces all stale or missing indexes together with the
global registry in one rollback set. The requested project still selects the
command result, while byte-identical current indexes remain unchanged. It
restores the exact prior missing/existing state on a failed TraceLane
transaction. `verify` is read-only: it derives the authenticated closure again
and requires the persisted project indexes and global registry to match; it
never repairs them. `list` and `find` both run that verified read-only query
path and accept the same deterministic filters. `--clean` excludes future
controls, while approved fact/domain filters use the review-authorized
effective scope.

Import, review append, rebuild, and verified public readers cooperate through
one physical-root lock, so readers observe a complete before or after
generation rather than a replacement gap. Ownership-aware cleanup will not
delete a path replaced by another writer. Uncooperative external processes are
not serialized by this protocol; they may cause a safe failure or leave their
own competing state, so atomicity claims apply to normal TraceLane writers
rather than arbitrary filesystem mutation.
