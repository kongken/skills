# Reading the task graph from any tracker

`docs/agents/issue-tracker.md` (written by `/setup-matt-pocock-skills`) tells you which tracker this repo uses and how to create, comment on and close issues there. Follow it for every **write**.

It doesn't say how to **read** a spec's tickets or a ticket's blockers, so those reads are below. Each tracker may lack a capability (older `/to-tickets` output, a free tier, an older CLI). So probe first and fall back in order: native relationship → the ticket body's text → ask the user to confirm the list.

Check flags against the installed CLI's `--help` before relying on them. Older tracker docs contain flags that newer CLIs dropped.

Whatever the tracker:
- **Exclude the spec itself.** It carries `ready-for-agent` too.
- **Show the user the graph** you read (tickets, blockers, ready set) before the first dispatch, so a misread edge is caught early.

## GitHub (`gh`)

- **Tickets of a spec:**
  - Run `gh issue view <spec> --json subIssues --jq '.subIssues.nodes[] | {number, title, state}'`. It returns at most 100.
  - If that's empty, the tickets may predate sub-issue support. Search for issues whose body has `## Parent` referencing the spec: `gh issue list --search "<spec number> in:body" --json number,title,body,state`, then keep the ones whose `## Parent` section names the spec.
- **Blockers of a ticket:**
  - Run `gh issue view <n> --json blockedBy --jq '.blockedBy.nodes[] | {number, state}'`. The result **includes closed blockers**, so filter by `state`.
  - Fall back to the `## Blocked by` section of the body.
- **Close:** `gh issue close <n> --comment "<what landed, where>"`.
- **REST equivalents**, if `gh` is too old:
  - `GET /repos/{o}/{r}/issues/{n}/sub_issues`
  - `GET /repos/{o}/{r}/issues/{n}/dependencies/blocked_by`

## GitLab (`glab`)

- **Tickets of a spec:**
  - GitLab issues can't have child issues, so a ticket points at its spec only through the `## Parent #<spec>` text in its description.
  - List candidates, for example by `ready-for-agent` label or milestone: `glab issue list --label ready-for-agent --output json` (check the output flag in `--help`).
  - Keep the ones whose description's `## Parent` names the spec.
- **Blockers of a ticket:**
  - Run `glab api projects/:id/issues/<iid>/links` and keep the entries whose `link_type` is `is_blocked_by`, checking their `state`.
  - `blocks` / `is_blocked_by` links need GitLab Premium or higher. On the free tier, fall back to the `## Blocked by` section or `Blocked by: #n` lines in the description.
- **Close:** `glab issue note <iid> -m "<what landed, where>"`, then `glab issue close <iid>`.
- For a self-managed host, pass `--hostname` / set `GITLAB_HOST` if `glab` doesn't infer it from the remote.

## Local markdown (`.scratch/`)

- **Spec:** `.scratch/<feature>/spec.md`.
- **Tickets:** `.scratch/<feature>/issues/NN-<slug>.md`, numbered in dependency order.
- **Blockers:** the `**Blocked by:**` line. It may hold ticket numbers *or titles*, so match both. A bare `Blocked by: NN, NN` also occurs.
- **Close:** the format defines no closed state. Change the ticket's `**Status:**` line to `done` and append a `## Resolution` note naming the integration branch and commits. Tell the user you used this convention.
- These files live in your main checkout, outside the run's worktrees, so editing them is a tracker write, not code.

## Anything else (Jira, Linear, …)

`docs/agents/issue-tracker.md` describes the workflow in prose. Interpret it to read the spec, its tickets and their blockers. If it can't express blockers, propose an order to the user and get it confirmed before dispatching.

## The frontier during a run

Read the graph **once**, at the start. After that, a ticket is ready when every one of its blockers has **landed** on the integration branch, according to the `land` results and `status`. Tracker state lags behind: a blocker usually isn't closed until the whole run ends.
