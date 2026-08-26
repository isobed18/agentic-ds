# What protects `main`, and what deliberately does not

Two agents commit to `main` directly, many times a day, by design. That rules
out the usual protection -- requiring a pull request, or requiring a status
check before a push -- because a required check cannot pass on a commit that
has not been pushed yet, so it blocks direct commits entirely. Turning that on
would not make the work safer; it would move all of it to a fork and leave
`main` protected and empty.

So the protection here is aimed at the failures that are actually irreversible:

| Rule | Stops |
|---|---|
| Force-push blocked | Rewriting history under the other agent's clone; losing commits that only exist on the remote |
| Deletion blocked | Deleting the branch outright |
| CI on every push to `main` | A breakage staying invisible until someone happens to run the suite |
| CI required on pull requests | An outside contribution merging red |

A red `main` is recoverable in a minute. A force-pushed `main` with the other
agent's work on it is not, which is why that is the one thing the remote
refuses.

Applied with:

```
gh api -X PUT repos/:owner/:repo/branches/main/protection \
  --input .github/branch-protection.json
```
