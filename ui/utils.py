def make_arrow_safe(df):
    df = df.copy()
    for col in df.columns:
        if "timedelta" in str(df[col].dtype):
            df[col] = df[col].astype(str)
    return df
