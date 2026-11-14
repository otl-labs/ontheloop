"""Shared AWS profile resolution for boto3.Session(profile_name=...).

Centralises the choice between a named ~/.aws/config profile (local and
Claude-Code-on-the-web development) and the boto3 default credential chain
(AWS Lambda execution role; GitHub-hosted CI runners using OIDC, which export
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN into the
environment).

A named profile does not exist on a hosted runner or inside Lambda, so forcing
one raises botocore ProfileNotFound. Returning None lets boto3 fall through to
the ambient credentials in those environments while preserving the named-profile
behaviour for local and web development.

An agent environment's host may also export placeholder environment credentials that its egress proxy replaces
(Decision 256). Those are not usable as they stand, so a named profile that carries its own credential_process in the
shared config file still wins over them; a CI runner or Lambda has no such profile.
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path

DEFAULT_SSO_PROFILE = "agent_platform"


def _self_sufficient_profile(name: str) -> bool:
    """True when the shared config file defines *name* with a credential_process; any read error counts as absent."""
    path = os.environ.get("AWS_CONFIG_FILE") or str(Path(os.environ.get("HOME", "~")).expanduser() / ".aws" / "config")
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return False
    section = "default" if name == "default" else f"profile {name}"
    return parser.has_option(section, "credential_process")


def resolve_aws_profile(explicit: str | None = None, default: str | None = DEFAULT_SSO_PROFILE) -> str | None:
    """Return the profile name for boto3.Session(profile_name=...), or None for the default chain.

    Returns None when running in AWS Lambda (AWS_LAMBDA_FUNCTION_NAME set). With environment credentials present
    (AWS_ACCESS_KEY_ID set: a CI runner's OIDC keys, or an agent host's proxy placeholders) the chosen name (explicit
    override, then AWS_PROFILE, then the supplied default) is returned only when the shared config file defines it with
    a credential_process, else None, so a stray override cannot reintroduce ProfileNotFound on a hosted runner.
    Otherwise returns the chosen name (local/web dev).
    """
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return None
    name = explicit or os.environ.get("AWS_PROFILE") or default
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        return name if name and _self_sufficient_profile(name) else None
    return name
