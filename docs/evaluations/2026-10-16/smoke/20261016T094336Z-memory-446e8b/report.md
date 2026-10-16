# Controlled memory diagnostic

Synthetic fixed-transcript extraction, not end-to-end task success. All summary calls count toward usage. Missing checkpoints are unscored, not successful null answers.

| Strategy | Correct / scored / planned fields | Calls | Known tokens | Unknown usage | Fabricated unknowns | Old values |
| --- | --- | --- | --- | --- | --- | --- |
| full | 24/24/24 | 3 | 3212 | 0 | 0 | 0 |
| recent10 | 9/24/24 | 3 | 1364 | 0 | 0 | 0 |
| summary | 21/24/24 | 6 | 5446 | 0 | 0 | 0 |
