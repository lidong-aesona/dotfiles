Where the code lives, and where you work

**The ticket is not the authority on this.** Tickets in this estate are written in <!-- ESTATE: the tracker repository --> and name its paths even for work that belongs elsewhere. Read the ticket for *what* to build and treat the rule below as authoritative for *where the files go*.

<!-- ESTATE: one entry per repository. -->
**`REPO`** (`/ABSOLUTE/PATH`, base `BRANCH`) - <!-- what belongs here, as concrete kinds of file -->

**The test when a file is ambiguous**: <!-- ESTATE: one question about what the file CONTAINS that sorts it into exactly one repository. -->

**A ticket that needs more than one repository makes one pull request in each**, and its closing comment names all of them. Every branch you create carries the ticket number: `build/<n>-<slug>`.

**A change made outside git still has its source of truth checked in.** If you change live infrastructure by hand, the resulting definition is committed in the same pass, alongside the evidence. A change that exists only in the live system and a ticket comment is not done.
