# API Catalog Example

Example showcasing multi-agent open discovery using a single base URL and an [API Catalog](https://www.ietf.org/archive/id/draft-ietf-httpapi-api-catalog-08.html). This example defines multiple agents under the same domain and exposes their metadata via `.well-known/api-catalog.json`.

The agents provided in this example are minimal and behave similarly to the one in the `helloworld` example — they simply return predefined `Message` events in response to a request. The focus of this example is not on agent logic, but on demonstrating multi-agent discovery and resolution using an [API Catalog](https://www.ietf.org/archive/id/draft-ietf-httpapi-api-catalog-08.html).

## Getting started

1. Start the server

   ```bash
   uv run .
   ```

2. Run the test client

   ```bash
   uv run test_client.py
   ```
