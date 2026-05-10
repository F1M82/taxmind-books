"""Service layer.

Per `docs/REPO_LAYOUT.md` §"Module boundaries", services own business
logic and call the model layer. Routes (`app.api.v1.*`) call services;
services never call routes.
"""
