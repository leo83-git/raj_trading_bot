# Quant Trading Bot Architecture Flowchart

## Overall System Architecture

```mermaid
graph TB
    subgraph "Data Layer"
        NSE[NSE Live Broker]
        DP[Data Provider]
        PC[Price Cache<br/>Background Refresh]
        NSEKit[NSEKit MCP<br/>F&O Data]
        TradingView[TradingView MCP<br/>Technical Analysis]
    end

    subgraph "Intelligence Layer"
        MA[Market Analysis<br/>Sentiment, Trends]
        NF[News Filter<br/>Event Awareness]
        NSEP[NSE Enrichment<br/>Fundamentals, FII/DII]
    end

    subgraph "Processing Layer"
        SC[Screener Engine<br/>AI Ranking]
        MD[Ensemble Models<br/>ML + DL + RL]
        ST[Strategy Engine<br/>Signal Generation]
    end

    subgraph "Decision Layer"
        PO[Portfolio Optimizer<br/>Asset Allocation]
        CA[Capital Allocator<br/>Position Sizing]
        RM[Risk Manager<br/>Limits & Controls]
    end

    subgraph "Execution Layer"
        EE[Execution Engine<br/>Smart Order Routing]
        SIM[Simulation Engine<br/>Paper Trading]
    end

    subgraph "Monitoring Layer"
        AT[Analytics Engine<br/>Performance Tracking]
        SPT[Strategy Tracker<br/>Win Rate Analysis]
        TL[Telegram Notifier<br/>Alerts & Reports]
    end

    subgraph "Specialized Modules"
        OES[Options Edge Selector<br/>Strategy Selection]
        SS[Short Straddle<br/>Time-based Options]
        BT[Backtester<br/>Historical Validation]
    end

    %% Data Flow
    NSE --> DP
    DP --> PC
    PC --> SC
    PC --> MD
    PC --> ST

    %% Intelligence Flow
    MA --> SC
    NF --> SC
    NSEP --> SC

    %% Processing Flow
    SC --> MD
    MD --> ST
    ST --> PO

    %% Decision Flow
    PO --> CA
    CA --> RM
    RM --> EE

    %% Execution Flow
    EE --> SIM
    SIM --> AT

    %% Monitoring Flow
    AT --> SPT
    SPT --> ST
    AT --> TL

    %% Specialized Flow
    ST --> OES
    ST --> SS
    ST --> BT

    %% External Data
    NSEKit -.-> DP
    TradingView -.-> MA
```

## Main Trading Loop Flowchart

```mermaid
flowchart TD
    START([System Start]) --> INIT[Initialize All Layers]

    INIT --> MARKET_CHECK{Market Open?<br/>9:15-15:15}

    MARKET_CHECK -->|No| WAIT[Wait 60 seconds]
    WAIT --> MARKET_CHECK

    MARKET_CHECK -->|Yes| DATA_FETCH[Fetch Market Data<br/>Quotes & Candles]

    DATA_FETCH --> CACHE_UPDATE[Update Price Cache<br/>Background Refresh]

    CACHE_UPDATE --> INTELLIGENCE[Run Intelligence<br/>Market Sentiment]

    INTELLIGENCE --> EVENT_CHECK{Event Blackout?<br/>High-impact news}

    EVENT_CHECK -->|Yes| SKIP_CYCLE[Skip Trading Cycle<br/>Wait for next cycle]

    EVENT_CHECK -->|No| SCREENING[Screen Stocks<br/>AI Ranking Top 50]

    SCREENING --> MODEL_PREDICT[Ensemble Model<br/>ML + DL + RL Scores]

    MODEL_PREDICT --> STRATEGY_SELECT[Strategy Engine<br/>Generate Signals]

    STRATEGY_SELECT --> RISK_CHECK{Risk OK?<br/>Position limits,<br/>capital available}

    RISK_CHECK -->|No| LOG_SKIP[Log Risk Skip<br/>Continue to next]

    RISK_CHECK -->|Yes| CATEGORY_CHECK{Trade Category}

    CATEGORY_CHECK -->|Equity| EXECUTE_EQUITY[Execute Equity Trade<br/>via Simulation]

    CATEGORY_CHECK -->|F&O| OPTIONS_EDGE[Options Edge Analysis<br/>IV, Theta, Strategy]

    OPTIONS_EDGE --> FNO_EXECUTE[Execute F&O Trade<br/>Multi-leg options]

    EXECUTE_EQUITY --> POSITION_MGMT[Position Management<br/>Monitor & Exit]

    FNO_EXECUTE --> POSITION_MGMT

    POSITION_MGMT --> TELEGRAM_ALERT[Send Trade Alerts<br/>Entry/Exit Notifications]

    TELEGRAM_ALERT --> ANALYTICS[Update Analytics<br/>P&L Tracking]

    ANALYTICS --> STRATEGY_TRACK[Update Strategy Tracker<br/>Win Rate Analysis]

    STRATEGY_TRACK --> CYCLE_COMPLETE[Trading Cycle Complete]

    CYCLE_COMPLETE --> LOG_SUMMARY[Log Trade Summary<br/>Open positions, P&L]

    LOG_SUMMARY --> MARKET_CLOSE{Market Close?<br/>3:20 PM}

    MARKET_CLOSE -->|No| WAIT

    MARKET_CLOSE -->|Yes| CLOSE_POSITIONS[Force Close All Positions<br/>Market closure]

    CLOSE_POSITIONS --> DAILY_REPORT[Generate Daily Report<br/>Performance summary]

    DAILY_REPORT --> MODEL_TRAIN[Auto-train Models<br/>Performance feedback]

    MODEL_TRAIN --> SHUTDOWN[System Shutdown]

    LOG_SKIP --> NEXT_STOCK[Process Next Stock]
    NEXT_STOCK --> STRATEGY_SELECT

    SKIP_CYCLE --> WAIT
```

## Data Processing Flowchart

```mermaid
flowchart TD
    START([Stock Data Processing]) --> CONFIG_LOAD[Load Watchlist Config<br/>Indices + Stocks]

    CONFIG_LOAD --> UNIQUE_SYMBOLS[Extract Unique Symbols<br/>Remove duplicates]

    UNIQUE_SYMBOLS --> MARKET_DATA_LOOP{For each symbol}

    MARKET_DATA_LOOP --> QUOTE_FETCH[Fetch Live Quote<br/>NSE API]

    QUOTE_FETCH --> CANDLE_FETCH[Fetch Candles<br/>5-minute data]

    CANDLE_FETCH --> OI_PCR_CHECK{Index Symbol?<br/>NIFTY/BANKNIFTY}

    OI_PCR_CHECK -->|Yes| OI_PCR_FETCH[Fetch OI/PCR Data<br/>Put/Call ratios]

    OI_PCR_CHECK -->|No| SKIP_OI[Skip OI/PCR]

    OI_PCR_FETCH --> CATEGORY_LOOP
    SKIP_OI --> CATEGORY_LOOP

    CATEGORY_LOOP -->|For each category| INDICATORS_CALC[Calculate Technical Indicators<br/>RSI, MACD, BB, etc.]

    INDICATORS_CALC --> FEATURES_BUILD[Build Feature Set<br/>Symbol, close, volume, category]

    FEATURES_BUILD --> OI_FEATURES_CHECK{Has OI/PCR data?}

    OI_FEATURES_CHECK -->|Yes| ADD_OI_FEATURES[Add OI/PCR Features<br/>PCR, OI ratios]

    OI_FEATURES_CHECK -->|No| STOCKS_DATA_APPEND

    ADD_OI_FEATURES --> STOCKS_DATA_APPEND[Append to stocks_data list<br/>With all features]

    STOCKS_DATA_APPEND --> CATEGORY_LOOP

    CATEGORY_LOOP -->|Next category| CATEGORY_LOOP

    CATEGORY_LOOP -->|All categories done| MARKET_DATA_LOOP

    MARKET_DATA_LOOP -->|All symbols processed| END_DATA_PROCESSING([Data Processing Complete])

    END_DATA_PROCESSING --> SCREENING_START([Start Screening Phase])
```

## Position Management Flowchart

```mermaid
flowchart TD
    START([Position Management]) --> POSITIONS_LIST[Get Open Positions<br/>From Simulation Engine]

    POSITIONS_LIST --> POS_LOOP{For each position}

    POS_LOOP --> SYMBOL_EXTRACT[Extract Symbol & Action<br/>BUY/SELL, quantity]

    SYMBOL_EXTRACT --> PRICE_FETCH[Fetch Current Price<br/>Option Chain or Quote]

    PRICE_FETCH --> PRICE_VALID{Valid Price?<br/>Not 0 or 100}

    PRICE_VALID -->|No| LOG_INVALID[Log Invalid Price<br/>Skip exit check]

    PRICE_VALID -->|Yes| AGE_CALC[Calculate Position Age<br/>Entry time vs now]

    AGE_CALC --> TARGET_CHECK[Get Target & Stop Loss<br/>From position metadata]

    TARGET_CHECK --> LEVELS_VALID{Valid Levels?<br/>Target > Entry for BUY<br/>Stop < Entry for BUY}

    LEVELS_VALID -->|No| RECALC_LEVELS[Recalculate Levels<br/>Target=Entry*1.05<br/>Stop=Entry*0.95]

    LEVELS_VALID -->|Yes| CONTINUE_CHECK

    RECALC_LEVELS --> CONTINUE_CHECK[Continue with levels]

    CONTINUE_CHECK --> EXIT_CONDITION_CHECK{Exit Condition Met?<br/>Price >= Target OR<br/>Price <= Stop Loss}

    EXIT_CONDITION_CHECK -->|Yes| EXIT_POSITION[Execute Position Exit<br/>Market order at current price]

    EXIT_CONDITION_CHECK -->|No| PROFIT_CHECK{Unrealized Profit > 10%?}

    PROFIT_CHECK -->|Yes| TRAILING_STOP[Update Trailing Stop Loss<br/>Lock in profits]

    PROFIT_CHECK -->|No| NEXT_POSITION[Process Next Position]

    EXIT_POSITION --> RECORD_OUTCOME[Record Trade Outcome<br/>P&L, Win/Loss]

    RECORD_OUTCOME --> SEND_ALERT[Send Exit Alert<br/>Telegram notification]

    SEND_ALERT --> NEXT_POSITION

    TRAILING_STOP --> NEXT_POSITION

    LOG_INVALID --> NEXT_POSITION

    NEXT_POSITION --> POS_LOOP

    POS_LOOP -->|All positions processed| END_MGMT([Position Management Complete])
```

## F&O Processing Flowchart

```mermaid
flowchart TD
    START([F&O Trade Processing]) --> SYMBOL_CHECK{Index F&O?<br/>NIFTY/BANKNIFTY}

    SYMBOL_CHECK -->|Yes| OPTIONS_EDGE[Run Options Edge Analysis<br/>IV percentile, Theta decay]

    SYMBOL_CHECK -->|No| STOCK_FNO[Stock F&O Processing]

    OPTIONS_EDGE --> STRATEGY_DECISION{Strategy Approved?<br/>Not NEUTRAL}

    STRATEGY_DECISION -->|No| SKIP_FNO[Skip F&O Trade<br/>Log neutral reason]

    STRATEGY_DECISION -->|Yes| CONFIDENCE_CHECK{Confidence > 0.15?<br/>For stocks}

    CONFIDENCE_CHECK -->|No| SKIP_FNO

    CONFIDENCE_CHECK -->|Yes| ATM_STRIKE[Calculate ATM Strike<br/>Round to nearest 100]

    ATM_STRIKE --> PREMIUM_FETCH[Fetch Option Premiums<br/>CE & PE at ATM]

    PREMIUM_FETCH --> LOT_SIZE[Get Lot Size<br/>From broker or defaults]

    LOT_SIZE --> CAPITAL_CHECK{Sufficient Capital?<br/>Premium * lots}

    CAPITAL_CHECK -->|No| SKIP_FNO

    CAPITAL_CHECK -->|Yes| POSITION_CHECK{Position Exists?<br/>Same symbol already held}

    POSITION_CHECK -->|Yes| SKIP_FNO

    POSITION_CHECK -->|No| EXECUTE_TRADE[Execute F&O Trade<br/>Create position in simulation]

    EXECUTE_TRADE --> ALERT_SEND[Send Trade Alert<br/>Entry notification]

    ALERT_SEND --> END_FNO([F&O Processing Complete])

    SKIP_FNO --> END_FNO

    STOCK_FNO --> CONFIDENCE_CHECK
```

## Key Components Legend

### Data Sources
- **NSE Live Broker**: Real-time market data
- **NSEKit MCP**: F&O chain data, expiry dates
- **TradingView MCP**: Technical analysis, backtesting
- **NSE Enrichment**: Fundamentals, FII/DII flows

### Processing Stages
- **Intelligence**: Market sentiment, event filtering
- **Screener**: Stock selection using AI ranking
- **Models**: Ensemble of ML/DL/RL predictions
- **Strategy**: Signal generation from model scores
- **Portfolio**: Position sizing and allocation
- **Risk**: Trade limits and drawdown control
- **Execution**: Order routing and simulation
- **Analytics**: Performance tracking and reporting

### Specialized Features
- **Options Edge**: Advanced options strategy selection
- **Short Straddle**: Time-based options strategies
- **Strategy Tracker**: Performance-based strategy suppression
- **Backtester**: Historical strategy validation

### Risk Management
- **Position Limits**: Max positions, exposure limits
- **Capital Allocation**: Dynamic position sizing
- **Stop Losses**: ATR-based trailing stops
- **Drawdown Control**: Daily loss limits
- **Event Filtering**: Skip trading during high-impact events

This flowchart represents the complete architecture and flow of the Quant Trading Bot system.