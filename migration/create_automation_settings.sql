-- Manual SQL to create user_automation_settings table
CREATE TABLE IF NOT EXISTS user_automation_settings (
    id SERIAL PRIMARY KEY,
    user_id INTEGER UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    is_enabled BOOLEAN DEFAULT FALSE,
    session_duration INTEGER DEFAULT 3600,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_user_automation_settings_id ON user_automation_settings (id); 