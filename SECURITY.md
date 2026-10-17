# Security policy

Dervo runs coding agents on your machine with access to your project folders and provider sign-ins, so security
reports matter a lot to us.

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub's private vulnerability reporting](https://github.com/brambach/dervo/security/advisories/new). Don't open a
public issue.

Include what you found, how to reproduce it and what an attacker could do with it. You'll get an acknowledgement as
soon as the report is read.

## Scope

In scope: the app in this repository, including the native shell, the Python host, the interface, the release signer
and the Supabase schema in `supabase/`.

Out of scope: Claude Code, Codex and other third-party tools Dervo starts. Please report those to their maintainers.
