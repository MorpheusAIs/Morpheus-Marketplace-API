# Model Sync Configuration

The application now includes automatic model synchronization to keep the local `models.json` file up-to-date with the active models from the Morpheus network.

## Configuration Options

Add these environment variables to your `.env` file or system environment:

### `MODEL_SYNC_ENABLED`
- **Default**: `True`
- **Description**: Master switch to enable/disable all model synchronization features
- **Values**: `True` or `False`

### `MODEL_SYNC_ON_STARTUP`
- **Default**: `True`
- **Description**: Whether to sync models when the application starts up
- **Values**: `True` or `False`
- **Note**: Only works if `MODEL_SYNC_ENABLED=True`

### `MODEL_SYNC_INTERVAL_HOURS`
- **Default**: `24`
- **Description**: How often to sync models in the background (in hours)
- **Values**: Any positive integer
- **Note**: Only works if `MODEL_SYNC_ENABLED=True`

### `ACTIVE_MODELS_URL`
- **Default**: `https://active.mor.org/active_models.json`
- **Description**: URL to fetch active models from
- **Values**: Any valid HTTP/HTTPS URL

## Example Configuration

```bash
# Enable model sync (default)
MODEL_SYNC_ENABLED=True

# Sync models on startup (default)
MODEL_SYNC_ON_STARTUP=True

# Sync models every 12 hours instead of default 24
MODEL_SYNC_INTERVAL_HOURS=12

# Use default active models URL
ACTIVE_MODELS_URL=https://active.mor.org/active_models.json
```

## Disabling Model Sync

To completely disable automatic model synchronization:

```bash
MODEL_SYNC_ENABLED=False
```

Or to disable only startup sync but keep background sync:

```bash
MODEL_SYNC_ON_STARTUP=False
MODEL_SYNC_ENABLED=True
MODEL_SYNC_INTERVAL_HOURS=24
```

## Logging

The model sync service provides detailed logging:

- `INFO` level: Normal sync operations and status
- `WARNING` level: Sync failures that don't prevent startup
- `ERROR` level: Critical sync errors

Look for log messages with patterns like:
- `"Starting model synchronization..."`
- `"✅ Model sync completed successfully"`
- `"❌ Model sync failed"`

## Manual Sync

You can still manually sync models using the standalone script:

```bash
python3 scripts/sync_models.py
```

## AWS Deployment

For AWS deployments, set these environment variables in your:

1. **ECS Task Definition** (if using ECS)
2. **Lambda Environment Variables** (if using Lambda)
3. **EC2 Instance Environment** (if using EC2)
4. **Systems Manager Parameter Store** (for secure configuration)

Example for ECS Task Definition:

```json
{
  "environment": [
    {
      "name": "MODEL_SYNC_ENABLED",
      "value": "True"
    },
    {
      "name": "MODEL_SYNC_INTERVAL_HOURS",
      "value": "12"
    }
  ]
}
```

## Troubleshooting

### Sync Fails on Startup
- Check network connectivity to `https://active.mor.org/active_models.json`
- Verify the URL is accessible from your deployment environment
- Check application logs for specific error messages

### Background Sync Not Working
- Ensure `MODEL_SYNC_ENABLED=True`
- Check that the application stays running (background sync stops if app restarts)
- Monitor logs for sync attempts every N hours

### Models Not Updating
- Verify the sync is actually running (check logs)
- Ensure the `models.json` file is writable by the application
- Check that the ModelRouter is loading from the correct file path

## Security Considerations

- The active models URL should use HTTPS
- Ensure your deployment environment can access external URLs
- Consider using a private mirror of the active models if needed for security
- The sync process creates backups of `models.json` before updating 