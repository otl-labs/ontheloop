# PlatformDev, PlatformAdmin and their five inline policies are owned by terraform/bootstrap
# (platform_roles.tf and its platform_*_policies.tf siblings, admin-applied). These removed blocks
# released them from this root state without destroying them (rec-3838,
# PLAN-platform-iam-personal-release). Never re-declare them here:
# tests/checks/iam_tf/test_platform_iam_admin_root_only.py fails the build.

removed {
  from = aws_iam_role.platform_dev

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_iam_role.platform_admin

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_iam_role_policy.platform_dev_runtime

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_iam_role_policy.platform_admin_ops

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_iam_role_policy.platform_admin_datalake

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_iam_role_policy.platform_admin_bootstrap_state

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_iam_role_policy.platform_admin_ducklake_breakglass

  lifecycle {
    destroy = false
  }
}
