#!/bin/bash
# Quick reference for downloading different timeframes
# File: scripts/download_timeframes.sh

PROJECT_ROOT="/home/rajasekhar/vibe-coding/raj_trading_bot"
SCRIPT="$PROJECT_ROOT/scripts/download_all_historical_enhanced.py"
LOG_DIR="$PROJECT_ROOT/download_logs"

mkdir -p "$LOG_DIR"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Historical Data Download Manager ===${NC}\n"

# Function to download a specific interval
download_interval() {
    local interval=$1
    local name=$2
    local estimated_time=$3
    
    echo -e "${YELLOW}Starting download: $name (estimated: $estimated_time)${NC}"
    echo "Command: INTERVAL=$interval python $SCRIPT"
    
    INTERVAL=$interval python "$SCRIPT" 2>&1 | tee "$LOG_DIR/download_${interval}.log"
    
    echo -e "${GREEN}✓ Completed: $name${NC}\n"
}

# Display menu
echo -e "${BLUE}Select option:${NC}"
echo "1) Download 1-minute candles   (WARNING: ~12 hours, 200GB)"
echo "2) Download 5-minute candles   (~8 hours, 50GB)"
echo "3) Download 15-minute candles  (~5 hours, 20GB)"
echo "4) Download 1-hour candles     (~3 hours, 5GB)"
echo "5) Download daily candles      (~3 hours, 1GB)"
echo "6) Download all (1h, 15m, day) (~11 hours)"
echo "7) Show download progress"
echo "8) Analyze skipped symbols"
echo ""

read -p "Enter choice [1-8]: " choice

case $choice in
    1)
        download_interval "minute" "1-minute candles" "12 hours"
        ;;
    2)
        download_interval "5minute" "5-minute candles" "8 hours"
        ;;
    3)
        download_interval "15minute" "15-minute candles" "5 hours"
        ;;
    4)
        download_interval "60minute" "1-hour candles" "3 hours"
        ;;
    5)
        download_interval "day" "Daily candles" "3 hours"
        ;;
    6)
        echo -e "${YELLOW}Starting batch download (3 timeframes)${NC}\n"
        
        download_interval "day" "Daily candles" "3 hours"
        sleep 5
        
        download_interval "60minute" "1-hour candles" "3 hours"
        sleep 5
        
        download_interval "15minute" "15-minute candles" "5 hours"
        
        echo -e "${GREEN}✓ All downloads completed!${NC}"
        ;;
    7)
        echo -e "${BLUE}=== Download Logs ===${NC}\n"
        ls -lh "$LOG_DIR"/download_*.log 2>/dev/null | tail -5
        echo ""
        echo "Latest 10 lines of current download:"
        tail -10 "$LOG_DIR"/download_*.log 2>/dev/null | tail -1
        ;;
    8)
        echo -e "${BLUE}=== Skipped Symbols Analysis ===${NC}\n"
        DATA_DIR="$PROJECT_ROOT/data"
        
        echo "Total skipped symbols by reason:"
        if [ -f "$DATA_DIR/skipped_symbols_day.log" ]; then
            echo -e "${YELLOW}Daily interval:${NC}"
            cut -d'|' -f2 "$DATA_DIR/skipped_symbols_day.log" | sort | uniq -c | sort -rn
        fi
        
        if [ -f "$DATA_DIR/skipped_symbols_15minute.log" ]; then
            echo -e "\n${YELLOW}15-minute interval:${NC}"
            cut -d'|' -f2 "$DATA_DIR/skipped_symbols_15minute.log" | sort | uniq -c | sort -rn
        fi
        
        echo ""
        echo "Sample of excluded instruments:"
        if [ -f "$DATA_DIR/skipped_symbols_day.log" ]; then
            head -5 "$DATA_DIR/skipped_symbols_day.log" | column -t -s'|'
        fi
        ;;
    *)
        echo "Invalid option"
        exit 1
        ;;
esac

echo -e "\n${BLUE}=== Database Info ===${NC}"
echo "DuckDB location: /media/rajasekhar/Backup/duckdb/historical_data.duckdb"
echo "Parquet location: /media/rajasekhar/Backup/duckdb/parquet/"
echo ""
echo "To query the database:"
echo "  python -c \"import duckdb; con=duckdb.connect('/media/rajasekhar/Backup/duckdb/historical_data.duckdb'); print(con.execute('SELECT COUNT(DISTINCT symbol) FROM candles').fetchall())\""
