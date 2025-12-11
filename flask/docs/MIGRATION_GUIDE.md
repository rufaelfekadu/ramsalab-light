# Database Migration Guide

I have updates the migration to be handled by Alembic and SQLAlchemy.

### Commands:

```bash
# Upgrade to latest migration
alembic upgrade head

# Create a new migration
alembic revision --autogenerate -m "Your migration message"

# Show migration history
alembic history

# Show current revision
alembic current

# Downgrade to base
alembic downgrade base
```

## Database Models

The application uses SQLAlchemy models defined in `app/models.py`:

- **User**: Stores user information (id, username, email, created_at)
- **Question**: Stores survey questions (id, text, question_type, options)
- **Response**: Stores user responses (id, user_id, question_id, response_type, response_value, file_path, timestamp)

## Migration Workflow

1. **Make changes to models** in `app/models.py`
2. **Create a migration** using `alembic revision --autogenerate -m "Description of changes"`
3. **Review the generated migration** in `migrations/versions/`
4. **Apply the migration** using `alembic upgrade head`
5. **Test your changes** to ensure everything works correctly

## Sample Data

The `populate_db.py` script creates sample questions for testing:

- Radio button question about learning preferences
- Checkbox question about skills to develop
- Text question about work environment
- Audio question about challenging projects

## Configuration

Database configuration is handled through environment variables (see `config.py`):

- `DB_HOST` - Database host (default: localhost)
- `DB_PORT` - Database port (default: 5432)
- `DB_NAME` - Database name (default: mbzuai_db)
- `DB_USER` - Database user (default: admin)
- `DB_PASSWORD` - Database password (default: securepassword)


## Development

When developing new features that require database changes:

1. Always create migrations for schema changes
2. Test migrations both up and down
3. Include sample data in migrations when appropriate
4. Document any breaking changes in migration comments
