# Time Series Analysis

This repository contains Python scripts and Jupyter notebooks for performing time series analysis, focusing on financial data and volatility modeling. It includes data downloading, processing, and analysis tools for stock market data.

## Scripts

- **download_data_yf.py**: Downloads financial data using yfinance.
- **get_data.py**: Retrieves and processes data.
- **get_index_data.py**: Fetches index-related data.
- **main.py**: Main entry point for running analyses.
- **technicalIndicators.py**: Implements technical indicators for time series data.
- **test.py**: Contains test functions.

## Notebooks

1. **LSTM_Volatality_GARCH.ipynb**  
    Implements LSTM and GARCH models for volatility prediction.

2. **LSTM_Volatality_GARCH2.ipynb**  
    An updated version of the LSTM and GARCH volatility modeling notebook.

3. **new_nb.ipynb**  
    A new notebook for experimenting with time series analysis techniques.

4. **nseData.ipynb**  
    Contains analysis and visualization of NSE (National Stock Exchange) data.

5. **quickstart.ipynb**  
    A quickstart guide to time series analysis with basic examples.

6. **test.ipynb**  
    A test notebook for trying out new ideas and debugging.

## Data

The `data/` folder contains sample CSV files with historical stock data for various tickers (e.g., BAJAJ-AUTO.NS, COALINDIA.NS) at different intervals (15m, 1h, 1d), as well as NSE bhav copies.

## Getting Started

1. Clone the repository:
    ```bash
    git clone <repository-url>
    cd time-series-analysis
    ```

2. Install dependencies:
    ```bash
    pip install -e .
    ```

3. Open the notebooks:
    ```bash
    jupyter notebook
    ```

## License

This project is licensed under the MIT License.  

## Acknowledgments

Special thanks to the contributors and the open-source community for their support.