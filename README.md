
# Instructions to Run the Project

## Prerequisites
- Ensure you have Python 3.10 or higher installed on your machine.
- Ensure you have a docker installed on your machine.
- Create and activate a virtual environment to manage dependencies. You can do this by running:
  ```
  python -m venv venv
  source venv/bin/activate  # On Windows use `venv\Scripts\activate`
  ```
- Install the required packages using pip. You can do this by running:
  ```
  pip install -r requirements.txt
  ```
- Run docker compose to start the required services:
  ```
  docker compose up -d
  ```

## Running the Project
1. Navigate to the project directory in your terminal.
2. Run the main server script using Python:
   ```
   uvicorn main:app
   ```
3. Open your web browser and go to `http://localhost:8000` to access the application.
4. ALso, if you want to play with some variables, edit the `local.env` file to set your desired configurations.
5. To stop the server, press `Ctrl + C` in the terminal.

## Additional Information
### Graceful Shutdown
- The server is configured to handle graceful shutdowns.
When you stop the server using `Ctrl + C`, it will try to close unclosed websocket connections. However, there are a debug delays, which you can configure in the `local.env` file.  
`FORCE_SHUTDOWN_TIMEOUT` controls how long the server will wait before forcing a shutdown.  
`REPORT_PROGRESS_INTERVAL` controls how often the server reports progress during shutdown.  
`DEBUG_CLEANUP_DELAY` delays a cleanup in seconds.  

