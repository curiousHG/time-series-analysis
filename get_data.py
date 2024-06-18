from datetime import date
from jugaad_data.nse import bhavcopy_save, bhavcopy_fo_save

# # Download bhavcopy
# bhavcopy_save(date(2020,1,1), "")

# # Download bhavcopy for futures and options
# bhavcopy_fo_save(date(2020,1,1), "/path/to/directory")

# Download stock data to pandas dataframe
from jugaad_data.nse import stock_df
df = stock_df(symbol="SBIN", from_date=date(2002,1,1),
            to_date=date(2023,6,10), series="EQ")

# save to csv
df.to_csv("SBIN.csv", index=False)