# GitHub Releases Integration

## Overview

The Morpheus Marketplace API now creates **GitHub Releases** automatically after successful deployment, ensuring consistent version tracking across both services (Lumerin Node and Marketplace API).

## How It Works

### Workflow: `.github/workflows/build.yml`

The `Create-Release` job (line 866-975) creates a GitHub Release **only after**:

1. ✅ **Docker image built** and pushed to GHCR
2. ✅ **ECS deployment** completes successfully  
3. ✅ **Health check** passes with correct version
4. ✅ **Database migrations** complete (if applicable)

### Release Creation

```yaml
- name: Create GitHub Release (After Successful Deployment)
  uses: actions/github-script@v7
  with:
    github-token: ${{ secrets.GITHUB_TOKEN }}
    script: |
      # Creates formal GitHub Release with:
      # - Tag name (e.g., v1.4.0 or v1.3.5-test)
      # - Release notes with deployment info
      # - Links to API docs and health check
      # - Container image reference
```

### Release Types

- **Production releases** (`main` branch):
  - Tag: `v1.4.0` (semantic versioning)
  - Prerelease: `false`
  - Example: https://api.mor.org

- **Test releases** (`test` branch):
  - Tag: `v1.3.5-test` (with `-test` suffix)
  - Prerelease: `true`
  - Example: https://api.dev.mor.org

## Benefits

### 1. Consistent Version Detection
Both Morpheus services now use the **same pattern** for version detection:
```
Terraform → GitHub Releases API → Filter by pattern → Deploy
```

### 2. Verified Deployments Only
Every release tag represents a **verified, working deployment**:
- ✅ Tests passed
- ✅ Image built successfully
- ✅ Deployment completed
- ✅ Health check verified
- ✅ No manual intervention needed

### 3. Terraform Integration
The `Morpheus-Infra` repository automatically fetches the latest release for deployment:

```hcl
# environments/03-morpheus_api/.terragrunt/00_data_github_release.tf
data "http" "github_releases" {
  url = "https://api.github.com/repos/MorpheusAIs/Morpheus-Marketplace-API/releases"
}
```

### 4. Deployment Safety
If deployment fails:
- ❌ No release is created
- ❌ No tag is pushed
- ✅ Safe to re-run workflow
- ✅ Database rollback is automatic

## Testing

### Verify Releases Endpoint
```bash
curl -s https://api.github.com/repos/MorpheusAIs/Morpheus-Marketplace-API/releases | jq '.[0:5] | .[] | .tag_name'
```

### Expected Output (after first deployment)
```
v1.4.0
v1.3.5-test
v1.3.2-test
v1.3.0
v1.2.6-test
```

## Migration Notes

### Before This Change
- ❌ Only Git tags were created (no formal releases)
- ❌ Terraform used `/tags` endpoint (inconsistent with Lumerin Node)
- ❌ Tags created before deployment verification

### After This Change
- ✅ Full GitHub Releases with notes and metadata
- ✅ Terraform uses `/releases` endpoint (consistent across services)
- ✅ Releases only created after verified deployment

### First Deployment
The **next successful deployment** to `test` or `main` will create the first GitHub Release. After that:
- Terraform will automatically use the latest release
- Manual version tracking is no longer needed
- Both services use the same version detection pattern

## Related Files

- **Workflow**: `Morpheus-Marketplace-API/.github/workflows/build.yml`
- **Terraform**: `Morpheus-Infra/environments/03-morpheus_api/.terragrunt/00_data_github_release.tf`
- **Documentation**: `Morpheus-Infra/ai-docs/AUTO_VERSION_DETECTION.md`
- **Test Script**: `Morpheus-Infra/ai-docs/test-github-versions.sh`

## References

- [GitHub Releases API](https://docs.github.com/en/rest/releases/releases)
- [Morpheus-Lumerin-Node Releases](https://github.com/MorpheusAIs/Morpheus-Lumerin-Node/releases)
- [Morpheus-Marketplace-API Releases](https://github.com/MorpheusAIs/Morpheus-Marketplace-API/releases)

