import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import time
import json
from typing import Dict, Any, List

# Optimized Page configuration 
st.set_page_config(
    page_title="Webhook API Monitor",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"  # Simplified sidebar
)

# Streamlined CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2rem;
        font-weight: bold;
        color: #1f77b4;
        text-align: center;
        margin-bottom: 1.5rem;
    }
    
    .metric-card {
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        padding: 1rem;
        border-radius: 8px;
        color: white;
        text-align: center;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    
    .status-healthy { background: linear-gradient(90deg, #11998e 0%, #38ef7d 100%); }
    .status-error { background: linear-gradient(90deg, #ff9a9e 0%, #fecfef 100%); }
</style>
""", unsafe_allow_html=True)

class WebhookMonitor:
    def __init__(self, api_base_url: str = "http://localhost:8443"):
        self.api_base_url = api_base_url
        self.session = requests.Session()
        self.session.timeout = 10
    
    def test_connection(self) -> Dict[str, Any]:
        """Test connection to webhook API"""
        try:
            response = self.session.get(f"{self.api_base_url}/health")
            if response.status_code == 200:
                return {"status": "healthy", "data": response.json()}
            else:
                return {"status": "error", "message": f"HTTP {response.status_code}"}
        except requests.exceptions.RequestException as e:
            return {"status": "error", "message": str(e)}
    
    def get_production_metrics(self) -> Dict[str, Any]:
        """Get production metrics summary"""
        try:
            response = self.session.get(f"{self.api_base_url}/api/metrics/summary")
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"HTTP {response.status_code}"}
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}
    
    def get_uat_metrics(self) -> Dict[str, Any]:
        try:
            response = self.session.get(f"{self.api_base_url}/admin/uat/summary")
            if response.status_code == 200:
                return response.json().get("data", {})
            else:
                return {"error": f"HTTP {response.status_code}"}
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}
    
    def get_processed_transactions_stats(self) -> Dict[str, Any]:
        """Get processed transactions statistics"""
        try:
            response = self.session.get(f"{self.api_base_url}/admin/processed-transactions/stats")
            if response.status_code == 200:
                return response.json().get("data", {})
            else:
                return {"error": f"HTTP {response.status_code}"}
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}

    def get_uat_files(self, date: str = None) -> Dict[str, Any]:
        """Get UAT webhook files"""
        try:
            params = {"date": date} if date else {}
            response = self.session.get(f"{self.api_base_url}/admin/uat/files", params=params)
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"HTTP {response.status_code}", "files": [], "count": 0}
        except requests.exceptions.RequestException as e:
            return {"error": str(e), "files": [], "count": 0}

def main():
    """Optimized main dashboard function"""
    
    # Header
    st.markdown('<h1 class="main-header">🚀 Webhook API Monitor</h1>', unsafe_allow_html=True)
    
    # Simplified controls in header
    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    
    with col1:
        api_url = st.text_input("API URL", value="http://localhost:8443", help="Base URL của Webhook API")
        
    with col2:
        auto_refresh = st.checkbox("Auto Refresh", value=False)
        
    with col3:
        refresh_interval = st.selectbox("Refresh (s)", [10, 30, 60], index=1)
        
    with col4:
        if st.button("🔄 Refresh Now"):
            st.rerun()
    
    # Initialize monitor
    monitor = WebhookMonitor(api_url)
    
    # Connection test
    connection = monitor.test_connection()
    if connection["status"] != "healthy":
        st.error(f"❌ API Connection Error: {connection.get('message', 'Unknown error')}")
        st.stop()
    else:
        st.success("✅ API Connected")
    
    # Environment tabs
    tab1, tab2 = st.tabs(["🏭 Production", "🧪 UAT"])
    
    # Production Tab
    with tab1:
        render_production_dashboard(monitor)
    
    # UAT Tab  
    with tab2:
        render_uat_dashboard(monitor)
    
    # Auto refresh logic
    if auto_refresh:
        time.sleep(refresh_interval)
        st.rerun()

def render_production_dashboard(monitor: WebhookMonitor):
    """Render optimized production dashboard"""
    
    st.subheader("🏭 Production Environment")
    
    # Get data
    production_metrics = monitor.get_production_metrics()
    transaction_stats = monitor.get_processed_transactions_stats()
    
    if "webhook" not in production_metrics:
        st.error("Unable to load production metrics")
        return
    
    webhook_data = production_metrics["webhook"]
    
    # Key metrics row
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            "Total Requests (24h)",
            webhook_data.get("total_requests", 0)
        )
    
    with col2:
        success_rate = webhook_data.get("success_rate", 0)
        st.metric(
            "Success Rate",
            f"{success_rate}%",
            delta=f"{success_rate - 95:.1f}%" if success_rate >= 95 else None
        )
    
    with col3:
        avg_time = webhook_data.get("avg_process_time", 0)
        st.metric(
            "Avg Response Time",
            f"{avg_time:.3f}s"
        )
    
    with col4:
        total_transactions = webhook_data.get("total_transactions", 0)
        st.metric(
            "Total Transactions",
            total_transactions
        )
    
    st.markdown("---")
    
    # Charts section
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("� Transaction Statistics")
        if "error" not in transaction_stats and transaction_stats.get("counts_by_period"):
            counts = transaction_stats["counts_by_period"]
            
            chart_data = {
                "Period": ["Today", "Last 24h", "Last 7 Days", "Last 30 Days"],
                "Count": [
                    counts.get("today", 0),
                    counts.get("last_24_hours", 0),
                    counts.get("last_7_days", 0), 
                    counts.get("last_30_days", 0)
                ]
            }
            
            df = pd.DataFrame(chart_data)
            fig = px.bar(df, x="Period", y="Count", title="Processed Transactions by Period")
            fig.update_layout(template="plotly_white")
            st.plotly_chart(fig, width='stretch')
        else:
            st.info("No transaction statistics available")
    
    with col2:
        st.subheader("� Financial Overview")
        if "error" not in transaction_stats and transaction_stats.get("financial_stats_30_days"):
            fin_stats = transaction_stats["financial_stats_30_days"]
            
            # Simple metrics display
            st.write(f"**Total Amount (30 days):** {fin_stats.get('total_amount', 0):,.2f} VND")
            st.write(f"**Average Amount:** {fin_stats.get('average_amount', 0):,.2f} VND")
            st.write(f"**Max Transaction:** {fin_stats.get('max_amount', 0):,.2f} VND")
            
            # Transaction types breakdown
            if transaction_stats.get("transaction_types_7_days"):
                types = transaction_stats["transaction_types_7_days"]
                
                fig = go.Figure(data=[go.Pie(
                    labels=list(types.keys()),
                    values=[t["count"] for t in types.values()],
                    hole=0.4
                )])
                
                fig.update_layout(
                    title="Transaction Types (7 days)",
                    template="plotly_white",
                    showlegend=True
                )
                st.plotly_chart(fig, width='stretch')
        else:
            st.info("No financial data available")
    
    # Row 2: Recent Activity and Storage Info
    st.markdown("---")
    col3, col4 = st.columns(2)
    
    with col3:
        st.subheader("⚡ Recent Activity (24h)")
        if transaction_stats.get("recent_activity_24h"):
            recent = transaction_stats["recent_activity_24h"]
            
            # Display recent activity metrics
            col_a, col_b = st.columns(2)
            with col_a:
                st.metric("Transactions", recent.get("transaction_count", 0))
                st.metric("Total Amount", f"{recent.get('total_amount', 0):,.0f} VND")
            with col_b:
                st.metric("Batches", recent.get("batch_count", 0))
                # Calculate average amount per transaction
                avg_per_tx = recent.get('total_amount', 0) / max(recent.get('transaction_count', 1), 1)
                st.metric("Avg/Transaction", f"{avg_per_tx:,.0f} VND")
        else:
            st.info("No recent activity data available")
    
    with col4:
        st.subheader("💾 Storage & System")
        if transaction_stats.get("storage"):
            storage = transaction_stats["storage"]
            
            # Storage metrics
            col_a, col_b = st.columns(2)
            with col_a:
                st.metric("In Memory", f"{storage.get('total_in_memory', 0):,}")
                st.metric("In Database", f"{storage.get('total_in_database', 0):,}")
            with col_b:
                sync_status = storage.get("sync_status", "unknown")
                sync_color = "🟢" if sync_status == "synced" else "🟡"
                st.write(f"**Sync Status:** {sync_color} {sync_status.title()}")
                
                service_status = transaction_stats.get("service_status", "unknown")
                status_color = "🟢" if service_status == "healthy" else "🔴"
                st.write(f"**Service Status:** {status_color} {service_status.title()}")
        else:
            st.info("No storage information available")
    
    # Footer
    st.markdown("---")
    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


def render_uat_dashboard(monitor: WebhookMonitor):
    """Render optimized UAT dashboard"""
    
    st.subheader("🧪 UAT Environment")
    
    # Get data
    uat_metrics = monitor.get_uat_metrics()
    uat_files_data = monitor.get_uat_files()
    
    # UAT metrics row
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        uat_files_count = uat_files_data.get("count", 0)
        st.metric("UAT Files Today", uat_files_count)
    
    with col2:
        if "error" not in uat_metrics:
            requests_today = uat_metrics.get("uat_requests_today", 0)
            st.metric("Requests Today", requests_today)
        else:
            st.metric("Requests Today", "Error")
    
    with col3:
        if "error" not in uat_metrics:
            success_rate = uat_metrics.get("uat_success_rate", 0)
            st.metric("Success Rate", f"{success_rate}%")
        else:
            st.metric("Success Rate", "Error")
    
    with col4:
        if "error" not in uat_metrics:
            total_transactions = uat_metrics.get("uat_total_transactions", 0)
            st.metric("Total Transactions", total_transactions)
        else:
            st.metric("Total Transactions", "Error")
    
    st.markdown("---")
    
    # UAT Files section
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📁 UAT Files")
        
        if uat_files_data.get("files"):
            files = uat_files_data["files"]
            
            # Show recent files table
            df_files = pd.DataFrame(files[-10:])  # Last 10 files
            if not df_files.empty:
                display_df = df_files[["filename", "size", "modified"]].copy()
                display_df["size"] = display_df["size"].apply(lambda x: f"{x:,} bytes")
                st.dataframe(display_df, width='stretch', height=300)
            else:
                st.info("No files found")
        else:
            st.info("No UAT files available")
    
    with col2:
        st.subheader("📊 UAT Statistics")
        
        if "error" not in uat_metrics:
            # Separate Request and Transaction metrics according to database schema
            col_a, col_b = st.columns(2)
            
            with col_a:
                st.markdown("**📡 Request Statistics**")
                request_data = {
                    "Metric": ["Total Requests", "Successful Requests"],
                    "Count": [
                        uat_metrics.get("uat_requests_today", 0),
                        uat_metrics.get("uat_successful_requests", 0)
                    ]
                }
                df_requests = pd.DataFrame(request_data)
                
                fig_requests = px.bar(
                    df_requests,
                    x="Metric",
                    y="Count", 
                    title="UAT Request Stats",
                    color="Count",
                    color_continuous_scale="blues"
                )
                fig_requests.update_layout(template="plotly_white", showlegend=False)
                st.plotly_chart(fig_requests, width='stretch')
            
            with col_b:
                st.markdown("**💰 Transaction Statistics**")
                transaction_data = {
                    "Metric": ["Total Transactions", "Processed Trans", "Failed Trans"],
                    "Count": [
                        uat_metrics.get("uat_total_transactions", 0),
                        uat_metrics.get("uat_processed_transactions", 0), 
                        uat_metrics.get("uat_failed_transactions", 0)
                    ]
                }
                df_transactions = pd.DataFrame(transaction_data)
                
                fig_transactions = px.bar(
                    df_transactions,
                    x="Metric",
                    y="Count", 
                    title="UAT Transaction Stats",
                    color="Count",
                    color_continuous_scale="greens"
                )
                fig_transactions.update_layout(template="plotly_white", showlegend=False)
                st.plotly_chart(fig_transactions, width='stretch')
        else:
            st.error("Unable to load UAT statistics")
    
    # UAT Controls
    st.markdown("---")
    st.subheader("�️ UAT Controls")
    
    col1, col2 = st.columns(2)
    
    with col1:
        selected_date = st.date_input(
            "Filter by Date",
            value=datetime.now().date(),
            help="View UAT files for specific date"
        )
        
    with col2:
        if st.button("🔍 View Files for Date"):
            date_str = selected_date.strftime("%Y%m%d")
            date_files = monitor.get_uat_files(date=date_str)
            
            if date_files.get("files"):
                st.success(f"Found {date_files['count']} files for {date_str}")
                # Display files for selected date
                df_date_files = pd.DataFrame(date_files["files"])
                st.dataframe(df_date_files[["filename", "size", "modified"]], width='stretch')
            else:
                st.info(f"No files found for {date_str}")
    
    # Footer
    st.markdown("---")
    st.caption(f"UAT Environment - Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    main()