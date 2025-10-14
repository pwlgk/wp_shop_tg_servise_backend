# scripts/analyze_orders_only.py

import csv
import logging
from collections import defaultdict

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

ORDERS_CSV_PATH = 'orders.csv'

def analyze_orders():
    """
    Анализирует CSV-дамп заказов и выводит подробную статистику по продажам.
    """
    logger.info("--- Starting Order-Only Analysis ---")

    # --- Структуры для сбора данных ---
    total_orders = 0
    total_sales = 0.0
    successful_orders_count = 0
    order_status_counts = defaultdict(int)
    customer_identifiers = set() # Для подсчета уникальных клиентов
    product_sales = defaultdict(lambda: {'name': '', 'quantity': 0, 'total_revenue': 0.0})

    with open(ORDERS_CSV_PATH, mode='r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            total_orders += 1
            status = row.get('status', 'unknown')
            order_total_str = row.get('order_total', '0')
            customer_id = row.get('customer_id', '0')
            billing_email = row.get('billing_email', '').lower().strip()

            try:
                order_total = float(order_total_str)
            except (ValueError, TypeError):
                logger.warning(f"Skipping order row due to invalid total: {row}")
                continue

            order_status_counts[status] += 1
            
            # Определяем уникального покупателя
            if customer_id and customer_id != '0':
                customer_identifiers.add(f"user_{customer_id}")
            elif billing_email:
                customer_identifiers.add(f"guest_{billing_email}")

            # Анализируем продажи и товары только в "успешных" заказах
            if status in ['completed', 'processing']:
                total_sales += order_total
                successful_orders_count += 1
                
                # Собираем данные по товарам в заказе
                # WooCommerce экспорт может иметь до 21 line_item
                for i in range(1, 22):
                    product_name = row.get(f'Product Item {i} Name')
                    if not product_name:
                        break # Товары в заказе закончились

                    try:
                        product_id = int(row.get(f'Product Item {i} id', 0))
                        quantity = int(row.get(f'Product Item {i} Quantity', 0))
                        total = float(row.get(f'Product Item {i} Total', 0.0))
                        
                        if product_id > 0 and quantity > 0:
                            product_sales[product_id]['name'] = product_name
                            product_sales[product_id]['quantity'] += quantity
                            product_sales[product_id]['total_revenue'] += total

                    except (ValueError, TypeError):
                        continue

    # --- Вывод отчета ---
    print("\n" + "="*50)
    print(" " * 12 + "ОТЧЕТ ПО АНАЛИЗУ ЗАКАЗОВ")
    print("="*50 + "\n")

    print("📊 **Общая статистика**")
    print(f"   - Всего заказов (все статусы): {total_orders}")
    print(f"   - Всего уникальных покупателей (включая гостей): {len(customer_identifiers)}")
    print(f"   - Общая сумма продаж (статусы 'completed', 'processing'): {total_sales:,.2f} RUB")
    if successful_orders_count > 0:
        avg_order_value = total_sales / successful_orders_count
        print(f"   - Средний чек по успешным заказам: {avg_order_value:,.2f} RUB")

    print("\n" + "-"*50 + "\n")

    print("📈 **Распределение заказов по статусам**")
    for status, count in sorted(order_status_counts.items(), key=lambda item: item[1], reverse=True):
        print(f"   - {status.capitalize():<15}: {count} шт.")

    print("\n" + "-"*50 + "\n")

    print("🏆 **Топ-10 самых продаваемых товаров (по количеству)**")
    # Сортируем товары по количеству проданных единиц
    sorted_products = sorted(product_sales.items(), key=lambda item: item[1]['quantity'], reverse=True)

    if not sorted_products:
        print("   - Нет данных о проданных товарах.")
    else:
        print(f"{'ID Товара':<10} | {'Кол-во (шт.)':<12} | {'Название товара'}")
        print("-"*80)
        for product_id, data in sorted_products[:10]:
            name = data['name']
            if len(name) > 50: # Обрезаем длинные названия
                name = name[:47] + '...'
            print(f"{product_id:<10} | {data['quantity']:<12} | {name}")

    print("\n" + "="*50)
    print(" " * 18 + "АНАЛИЗ ЗАВЕРШЕН")
    print("="*50 + "\n")


if __name__ == "__main__":
    try:
        analyze_orders()
    except FileNotFoundError:
        logger.error(f"Ошибка: Файл '{ORDERS_CSV_PATH}' не найден. Убедитесь, что он находится в корне проекта.")
    except Exception:
        logger.error("Произошла непредвиденная ошибка:", exc_info=True)