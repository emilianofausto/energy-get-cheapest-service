# Energy Python Service (FastAPI)

The core analytical engine of the system. This service integrates with external APIs to fetch real-time energy data and provides the logic for determining the most cost-effective operation windows.

## Tech Stack
* **Language**: Python 3.12.
* **Framework**: FastAPI (Asynchronous).
* **Database**: PostgreSQL.
* **ORM**: SQLAlchemy with Lazy Initialization to optimize CI/CD pipeline performance.

## Key Functionalities
* **External Integration**: Fetches spot prices from `elprisetjustnu.se` for the SE3 grid zone.
* **Caching Layer**: Implements a PostgreSQL cache to minimize external API calls and improve response times for the optimization algorithm.
* **Optimization Algorithm**: Calculates the lowest cost window by multiplying appliance consumption by the upcoming hourly SEK/kWh rates.

## Deployment Context
Designed for deployment on AWS EKS within the Stockholm region (`eu-north-1`) to minimize latency for users in the Tyresö area.
