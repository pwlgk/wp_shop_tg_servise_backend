# scripts/analyze_data.py

import csv
import logging
from collections import defaultdict

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

USERS_CSV_PATH = 'users.csv'
ORDERS_CSV_PATH = 'orders.csv'

def analyze_data():
    """
    Анализирует CSV-дампы пользователей и заказов и выводит подробную статистику.
    """
    logger.info("--- Starting Data Analysis ---")

    # --- 1. Анализ пользователей ---
    users = {}
    total_users = 0
    with open(USERS_CSV_PATH, mode='r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            user_id = int(row.get('ID') or row.get('user_id', 0))
            if user_id > 0:
                users[user_id] = {
                    'display_name': row.get('display_name', 'N/A'),
                    'email': row.get('user_email', 'N/A')
                }
                total_users += 1
    logger.info(f"Found {total_users} unique users in users.csv.")

    # --- 2. Анализ заказов ---
    total_orders = 0
    total_sales_all_time = 0.0
    order_status_counts = defaultdict(int)
    customer_spending = defaultdict(lambda: {'total_spent': 0.0, 'order_count': 0})
    
    with open(ORDERS_CSV_PATH, mode='r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            total_orders += 1
            status = row.get('status', 'unknown')
            order_total_str = row.get('order_total', '0')
            customer_id_str = row.get('customer_id', '0')

            try:
                order_total = float(order_total_str)
                customer_id = int(customer_id_str)
            except (ValueError, TypeError):
                logger.warning(f"Skipping order row due to invalid data: {row}")
                continue

            order_status_counts[status] += 1
            
            # Считаем общую сумму продаж только по "успешным" статусам
            if status in ['completed', 'processing']:
                total_sales_all_time += order_total

            # Собираем статистику по клиентам, только по выполненным заказам
            if status == 'completed' and customer_id > 0:
                customer_spending[customer_id]['total_spent'] += order_total
                customer_spending[customer_id]['order_count'] += 1

    # --- 3. Вывод отчета ---
    print("\n" + "="*50)
    print(" " * 15 + "АНАЛИТИЧЕСКИЙ ОТЧЕТ")
    print("="*50 + "\n")

    print(f"👤 **Общая статистика по пользователям**")
    print(f"   - Всего зарегистрированных пользователей: {total_users}")
    customers_with_purchases = len(customer_spending)
    customers_without_purchases = total_users - customers_with_purchases
    print(f"   - Клиентов с покупками (хотя бы 1 выполненный заказ): {customers_with_purchases}")
    if total_users > 0:
        conversion_rate = (customers_with_purchases / total_users) * 100
        print(f"   - Конверсия в покупателя: {conversion_rate:.2f}%")
    print(f"   - Зарегистрированных, но без покупок: {customers_without_purchases}")

    print("\n" + "-"*50 + "\n")

    print(f"📦 **Общая статистика по заказам**")
    print(f"   - Всего заказов (все статусы): {total_orders}")
    print(f"   - Общая сумма продаж (статусы 'completed', 'processing'): {total_sales_all_time:,.2f} RUB")
    if total_orders > 0:
        avg_order_value = total_sales_all_time / (order_status_counts.get('completed', 0) + order_status_counts.get('processing', 0)) if (order_status_counts.get('completed', 0) + order_status_counts.get('processing', 0)) > 0 else 0
        print(f"   - Средний чек: {avg_order_value:,.2f} RUB")
    
    print("\n📈 **Распределение заказов по статусам:**")
    for status, count in sorted(order_status_counts.items(), key=lambda item: item[1], reverse=True):
        print(f"   - {status.capitalize():<15}: {count} шт.")

    print("\n" + "-"*50 + "\n")
    
    print("🏆 **Топ-10 клиентов по сумме выкупа (только 'completed')**")
    # Сортируем клиентов по сумме покупок
    sorted_customers = sorted(customer_spending.items(), key=lambda item: item[1]['total_spent'], reverse=True)
    
    if not sorted_customers:
        print("   - Нет данных о выполненных заказах.")
    else:
        print(f"{'ID Клиента':<12} | {'Имя / Email':<35} | {'Сумма выкупа':<15} | {'Кол-во заказов':<15}")
        print("-"*80)
        for i, (customer_id, data) in enumerate(sorted_customers[:10]):
            user_info = users.get(customer_id, {'display_name': 'Не найден', 'email': 'N/A'})
            display_name = user_info.get('display_name') or user_info.get('email')
            # Обрезаем имя, если оно слишком длинное
            if len(display_name) > 33:
                display_name = display_name[:30] + '...'
            
            total_spent_str = f"{data['total_spent']:,.2f} RUB"
            print(f"{customer_id:<12} | {display_name:<35} | {total_spent_str:<15} | {data['order_count']:<15}")
            
    print("\n" + "="*50)
    print(" " * 18 + "АНАЛИЗ ЗАВЕРШЕН")
    print("="*50 + "\n")


if __name__ == "__main__":
    try:
        analyze_data()
    except FileNotFoundError as e:
        logger.error(f"Ошибка: Файл не найден. Убедитесь, что '{e.filename}' находится в корне проекта.")
    except Exception as e:
        logger.error(f"Произошла непредвиденная ошибка: {e}", exc_info=True)