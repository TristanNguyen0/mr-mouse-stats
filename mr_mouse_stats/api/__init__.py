"""HTTP APIs. Two separate apps, deployed as separate Lambdas:

- `public`  — read-only, unauthenticated, serves the stats site
- `admin`   — owns every write, behind a Cognito JWT authorizer

The split is structural, not conventional: the public app never imports
anything that can write, and the admin app is the only place the four
mutating Store methods are reachable over HTTP.
"""
