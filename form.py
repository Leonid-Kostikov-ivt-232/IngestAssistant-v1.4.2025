import sys
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout,
    QComboBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel
)
from PyQt5.QtCore import Qt

class IngestForm(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Помощник инжеста")
        self.resize(400, 300)

        # Основной вертикальный лэйаут
        self.layout = QVBoxLayout(self)

        # Редактируемый выпадающий список журналистов
        self.comboJournalists = QComboBox()
        self.comboJournalists.setEditable(True)
        self.layout.addWidget(self.comboJournalists)

        # Текстовое поле для названия сюжета
        self.lineEditStoryName = QLineEdit()
        self.lineEditStoryName.setPlaceholderText("Введите название сюжета")
        self.layout.addWidget(self.lineEditStoryName)

        # Таблица с двумя колонками: Имя файла и Время
        self.tableFiles = QTableWidget()
        self.tableFiles.setColumnCount(2)
        self.tableFiles.setHorizontalHeaderLabels(["Имя файла", "Время файла"])
        self.tableFiles.setSelectionBehavior(QTableWidget.SelectRows)
        self.tableFiles.setSelectionMode(QTableWidget.ExtendedSelection)
        self.layout.addWidget(self.tableFiles)

        # Кнопка для запуска инжеста
        self.buttonIngest = QPushButton("Начать инжест")
        self.layout.addWidget(self.buttonIngest)

        # Метка статуса
        self.labelStatus = QLabel("Статус: ожидание")
        self.layout.addWidget(self.labelStatus)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    form = IngestForm()
    form.show()
    sys.exit(app.exec_())
