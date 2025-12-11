# Grafana Setup for MBZUAI Survey Dashboard

This directory contains Grafana configuration files for visualizing the survey database.

## Quick Start

1. **Start Grafana with Docker Compose:**
   ```bash
   docker compose up -d grafana
   ```

2. **Access Grafana:**
   - URL: http://localhost:3000
   - Default username: `admin`
   - Default password: `admin` (change on first login)

3. **Dashboard:**
   The dashboard will be automatically loaded and available in the Grafana UI.

## Dashboard Features

The dashboard includes the following visualizations:

1. **Key Metrics:**
   - Total Users
   - Total Surveys
   - Total Responses
   - Active Surveys

2. **Time Series Charts:**
   - Users Created Over Time
   - Responses Over Time
   - Surveys Started Over Time (by Survey)

3. **Distribution Charts:**
   - Responses by Type (Pie Chart)
   - Questions per Survey (Bar Chart)
   - Question Types Distribution (Bar Chart)

4. **Tables:**
   - Survey Completion Statistics
   - Top Users by Activity

## Customization

You can customize the dashboard by:
1. Logging into Grafana
2. Opening the "MBZUAI Survey Dashboard"
3. Clicking the gear icon → "Edit"
4. Making your changes
5. Saving the dashboard

Changes made in the UI will be saved to the Grafana database, not the JSON file. To persist changes to the file, export the dashboard and replace the JSON file.

## Troubleshooting

### Grafana can't connect to the database
- Ensure the `db` service is running: `docker compose ps`
- Check that the database credentials in the datasource match your `.env` file
- Verify the network connectivity: `docker compose exec grafana ping db`

### Dashboard not showing data
- Verify the datasource connection: Configuration → Data Sources → PostgreSQL → "Test" button
- Check that your database has data
- Ensure the time range in the dashboard includes your data

### Port already in use
- Change the `GRAFANA_PORT` in your `.env` file or `docker-compose.yml`
- Or stop any other service using port 3000

