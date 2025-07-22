import os
import re
import sys
import shutil
import tempfile
import subprocess
import configparser
import win32com.client # Для работы с дисками Windows


from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout,
    QLabel, QPushButton, QHBoxLayout, QTableWidgetItem,
    QMessageBox, QFileDialog, QLineEdit, QHeaderView,
    QStyledItemDelegate, QListWidget, QDialog, QDialogButtonBox) # Добавлено для диалога выбора флешки
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QIcon # Для иконок


from form import IngestForm
from datetime import datetime


class AlignCenterDelegate(QStyledItemDelegate):
    '''Класс делегата для колонки "Время файла".
    Используется для постоянного централизованного
    выравнивания содержимого колонки.'''

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.displayAlignment = Qt.AlignCenter
        

class SelectDriveDialog(QDialog):
    '''Диалог для выбора флешки, если найдено несколько'''
    def __init__(self, drives_info, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Выберите флешку")
        self.layout = QVBoxLayout(self)

        self.label = QLabel("Найдено несколько флешек с файлами MTS. Выберите одну:")
        self.layout.addWidget(self.label)

        self.list_widget = QListWidget()
        for drive_path, _, _ in drives_info:
            self.list_widget.addItem(drive_path)
        self.layout.addWidget(self.list_widget)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.layout.addWidget(self.buttons)

        self.selected_drive_index = -1

        if drives_info:
            self.list_widget.setCurrentRow(0) # Выбираем первую по умолчанию

    def accept(self):
        self.selected_drive_index = self.list_widget.currentRow()
        super().accept()


class CopyFilesWorker(QThread):
    '''Поток для копирования файлов mts, чтобы не блокировать GUI'''
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, files_to_copy, source_dir, dest_dir):
        super().__init__()
        self.files_to_copy = files_to_copy
        self.source_dir = source_dir
        self.dest_dir = dest_dir

    def run(self):
        try:
            os.makedirs(self.dest_dir, exist_ok=True)
            for filename in self.files_to_copy:
                src = os.path.join(self.source_dir, filename)
                dst = os.path.join(self.dest_dir, filename)
                shutil.copy2(src, dst)
            self.finished.emit(True, 'Копирование завершено успешно.')
        except Exception as e:
            self.finished.emit(False, f'Ошибка копирования: {e}')


class FFmpegWorker(QThread):
    '''Поток для выполнения ffmpeg, чтобы не блокировать GUI'''
    finished = pyqtSignal(bool, str)  # успех, сообщение
    
    def __init__(self, ffmpeg_cmd, output_path, move_to_path):
        super().__init__()
        self.ffmpeg_cmd = ffmpeg_cmd
        self.output_path = output_path
        self.move_to_path = move_to_path
        self.process = None  # Добавляем атрибут для хранения объекта процесса

    def run(self):
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            self.process = subprocess.Popen(
                self.ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True, # Декодировать stdout/stderr как текст
                encoding='utf-8',
                creationflags=creationflags
            )

            # Ожидаем завершения процесса и получаем stdout/stderr
            stdout, stderr = self.process.communicate()
            
            if self.process.returncode == 0:
                try:
                    shutil.move(self.output_path, self.move_to_path)
                    self.finished.emit(True, "Кодирование и перенос завершены успешно.")
                except Exception as e:
                    self.finished.emit(False, f"Кодирование завершено, но не удалось переместить файл mxf:\n{e}")
            else:
                self.finished.emit(False, f"FFmpeg вернул ошибку:\n{result.stderr}")
        except Exception as e:
            self.finished.emit(False, f"Ошибка запуска ffmpeg:\n{e}")


    def terminate_ffmpeg_process(self):
        '''
        Принудительно завершает процесс ffmpeg на Windows.
        Вызывается извне, например, при закрытии окна.
        '''
        if self.process and self.process.poll() is None:  # Если процесс еще жив
            self.process.terminate()  # Посылаем запрос на завершение процесса
            try:
                self.process.wait(timeout=5)  # Ждем до 5 секунд завершения
            except subprocess.TimeoutExpired:
                self.process.kill()  # Принудительно убиваем, если не завершился
            self.process = None


class IngestFormMain(IngestForm):

    def __init__(self):
        super().__init__()

        self.config = configparser.ConfigParser()
        self.ini_path = 'journalists.ini'
        self.directory = None # Путь к папке MTS на флешке
        # self.directory_ingest = None # Больше не нужен, путь инжеста из конфига
        self.now = datetime.now()
        self.day_month_table = self.now.strftime("%d.%m.%Y")
        
        # Загружаем путь для инжеста из config.ini
        self.load_ingest_config()
        
        # Удаляем comboJournalists из вертикального лэйаута,
        # чтобы потом добавить в горизонтальный лэйаут вместе с кнопкой
        self.layout.removeWidget(self.comboJournalists)
        
        self.h_layout = QHBoxLayout() # Первый горизонтальный лэйаут
        self.h_layout1 = QHBoxLayout() # Второй горизонтальный лэйаут
        self.h_layout2 = QHBoxLayout() # Третий горизонтальный лэйаут

        # Добавляем comboJournalists и кнопку в горизонтальный лэйаут
        self.h_layout.addWidget(self.comboJournalists, stretch=3)

        self.comboJournalists.setPlaceholderText('Выберите или введите ФИО журналиста')

        self.buttonAdd = QPushButton('Добавить журналиста')
        self.buttonAdd.adjustSize()
        self.buttonAdd.clicked.connect(self.add_journalist)
        self.h_layout.addWidget(self.buttonAdd, stretch=1)

        # Вставляем горизонтальный лэйаут в начало вертикального лэйаута
        self.layout.insertLayout(0, self.h_layout)
        
        # Поле для отображения выбранной директории с файлами mts (только для информации)
        self.lineSelectDirMts = QLineEdit()
        self.lineSelectDirMts.setPlaceholderText('Путь к файлам MTS будет определён автоматически')
        self.lineSelectDirMts.setReadOnly(True) # Только для чтения
        self.h_layout1.addWidget(self.lineSelectDirMts, stretch=3)

        # Кнопка для запуска автоматического поиска директории с файлами mts
        self.buttonSelectDir = QPushButton('Найти файлы MTS на флешке')
        self.buttonSelectDir.clicked.connect(self.select_directory)
        self.h_layout1.addWidget(self.buttonSelectDir, stretch=1)
        self.layout.insertLayout(3, self.h_layout1) # Добавим под журналистов

        self.tableFiles.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch) # растянуть первую колонку

        self.delegate = AlignCenterDelegate(self.tableFiles) # Создаем делегат
        self.tableFiles.setItemDelegateForColumn(1, self.delegate) # Выравниваем по центру время файла
        
        # Удалены элементы для выбора папки инжеста, т.к. она берётся из конфига
        # self.lineIngest и self.buttonSelectDirIngest не создаются

        self.buttonIngest.clicked.connect(self.start_ingest)
        
        # Загрузка журналистов из ini файла
        self.load_journalists(self.ini_path)
        self.select_directory() # Поиск флэшек при запуске программы
        self.worker_copy = None
        self.worker = None  # Сюда помещаем поток
        
        # Таймер для копирования и кодирования
        self.labelTimer = QLabel("00:00:00")
        font = self.labelTimer.font()
        font.setPointSize(14)
        self.labelTimer.setFont(font)
        self.labelTimer.setAlignment(Qt.AlignCenter)
        #self.h_layout2.addWidget(self.labelTimer)

        self.buttonStop = QPushButton("Стоп")
        self.buttonStop.setEnabled(True)
        #self.h_layout2.addWidget(self.buttonStop)
        
        # Таймер и счетчик времени
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_timer)
        self.elapsed_seconds = 0
        self.buttonStop.clicked.connect(self.handle_stop_pressed)
        
        self.layout.insertLayout(4, self.h_layout2)



    def load_ingest_config(self):
        '''Загрузка пути для инжеста из config.ini'''
        try:
            self.config.read('config.ini', encoding='utf-8')
            if 'settings' in self.config:
                self.ingest_root_path = self.config['settings'].get('ingest_root_path')
                self.mxf_target_folder = self.config['settings'].get('mxf_target_folder')
                if not self.ingest_root_path or not self.mxf_target_folder:
                    QMessageBox.critical(self, 'Ошибка конфигурации',
                                         'В config.ini должны быть указаны ingest_root_path и mxf_target_folder')
            else:
                QMessageBox.critical(self, 'Ошибка конфига',
                                     'Раздел [settings] не найден в config.ini')
                self.ingest_root_path = None
                self.mxf_target_folder = None
        except Exception as e:
            QMessageBox.critical(self, 'Ошибка чтения конфига',
                                 f'Не удалось прочитать config.ini: {e}')
            self.ingest_root_path = None
            self.mxf_target_folder = None


    def get_removable_drives(self):
        '''Получает список съемных (removable) дисков в системе.'''
        drives = []
        try:
            wmi = win32com.client.Dispatch("WbemScripting.SWbemLocator")
            for d in wmi.ConnectServer().ExecQuery('Select * from Win32_LogicalDisk where DriveType=2'):
                drives.append(d.DeviceID + '\\')
        except Exception as e:
            print(f'Ошибка получения списка дисков: {e}')
            QMessageBox.critical(self, 'Ошибка системы', 
                                 f'Не удалось получить список съемных дисков. Возможно, отсутствует WMI. Ошибка: {e}')
        return drives


    def find_mts_folder_on_drive(self, drive_path):
        '''Ищет папку PRIVATE\\AVCHD\\BDMV\\STREAM и файлы MTS внутри неё.'''
        mts_folder_path = os.path.join(drive_path, 'PRIVATE', 'AVCHD', 'BDMV', 'STREAM')
        found_files = []
        if os.path.isdir(mts_folder_path):
            try:
                # Фильтруем файлы по расширению .mts и паттерну 0000.mts
                pattern = re.compile(r'^\d{4}\.mts$', re.IGNORECASE)
                found_files = [f for f in os.listdir(mts_folder_path) if pattern.match(f)]
            except Exception as e:
                print(f'Ошибка чтения содержимого {mts_folder_path}: {e}')
        return mts_folder_path, found_files

    def select_directory(self):
        '''Автоматически определяет директорию с файлами MTS на флешке.'''
        removable_drives = self.get_removable_drives()
        
        if not removable_drives:
            QMessageBox.information(self, 'Поиск флешек', 'Съемные диски не найдены.')
            self.lineSelectDirMts.setPlaceholderText('Съемные диски не найдены.')
            return

        found_mts_sources = [] # Список [(drive_path, mts_folder_path, [mts_files])]

        for drive_path in removable_drives:
            mts_folder_path, mts_files = self.find_mts_folder_on_drive(drive_path)
            if mts_files: # Если папка существует и в ней есть MTS файлы
                found_mts_sources.append((drive_path, mts_folder_path, mts_files))

        if not found_mts_sources:
            QMessageBox.information(self, 'Поиск MTS', 'Файлы MTS не найдены ни на одной флешке.')
            self.lineSelectDirMts.setPlaceholderText('Файлы MTS не найдены на флешках.')
            self.tableFiles.clearContents()
            self.tableFiles.setRowCount(0)
            self.directory = None # Сбрасываем выбранную директорию
            return

        if len(found_mts_sources) > 1:
            # Если найдено несколько источников, предлагаем пользователю выбрать
            dialog = SelectDriveDialog(found_mts_sources, self)
            if dialog.exec_() == QDialog.Accepted:
                selected_index = dialog.selected_drive_index
                if selected_index != -1:
                    self.directory = found_mts_sources[selected_index][1] # Путь к папке STREAM
                    files_to_display = found_mts_sources[selected_index][2] # Список файлов
                else:
                    self.directory = None
                    QMessageBox.warning(self, 'Выбор отменен', 'Выбор флешки отменен.')
                    return
            else:
                self.directory = None
                QMessageBox.warning(self, 'Выбор отменен', 'Выбор флешки отменен.')
                return
        else:
            # Если найден только один источник, используем его
            self.directory = found_mts_sources[0][1] # Путь к папке STREAM
            files_to_display = found_mts_sources[0][2] # Список файлов

        self.lineSelectDirMts.setPlaceholderText(self.directory)
        self.labelStatus.setText(f'Выбрана папка: {self.directory}')

        # Очистим таблицу
        self.tableFiles.clearContents()
        self.tableFiles.setRowCount(0)

        # Заполняем таблицу
        self.tableFiles.setRowCount(len(files_to_display))
        for row, filename in enumerate(sorted(files_to_display)):
            item_name = QTableWidgetItem(filename)
            item_name.setFlags(item_name.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.tableFiles.setItem(row, 0, item_name)

            item_time = QTableWidgetItem(self.day_month_table)
            item_time.setFlags(item_time.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            # Выравнивание по центру для колонки времени будет сделано делегатом
            self.tableFiles.setItem(row, 1, item_time)

        # self.selected_directory = self.directory # Можно использовать self.directory
        self.selected_directory = self.directory


    def load_journalists(self, ini_path):
        '''Загрузка списка журналистов из файла journalists.ini'''
        self.config.read(ini_path, encoding='utf-8')

        if 'journalists' in self.config and 'names' in self.config['journalists']:
            names_str = self.config['journalists']['names']
            names = [name.strip() for name in names_str.split(',') if name.strip()]
            self.comboJournalists.addItems(names)
        else:
            # Создаем секцию, если её нет
            if not self.config.has_section('journalists'):
                self.config.add_section('journalists')
            self.config.set('journalists', 'names', '') # Инициализируем пустую строку

            # Сохраняем в ini файл, чтобы секция появилась
            with open(self.ini_path, 'w', encoding='utf-8') as configfile:
                self.config.write(configfile)
            print('Внимание: раздел [journalists] или ключ names не найден в ini файле. Создан пустой раздел.')


    def add_journalist(self):
        new_name = self.comboJournalists.currentText().strip()
        if not new_name:
            QMessageBox.warning(self, 'Ошибка', 'Имя журналиста не может быть пустым.')
            return

        # Убедимся, что секция journalists существует
        if not self.config.has_section('journalists'):
            self.config.add_section('journalists')
        
        # Получаем текущий список
        names_str = self.config['journalists'].get('names', '')
        names = [name.strip() for name in names_str.split(',') if name.strip()]

        if new_name in names:
            self.labelStatus.setText(f"Журналист '{new_name}' уже в списке.")
            return

        names.append(new_name)
        self.config['journalists']['names'] = ', '.join(names)

        # Сохраняем в ini файл
        try:
            with open(self.ini_path, 'w', encoding='utf-8') as configfile:
                self.config.write(configfile)
        except Exception as e:
            QMessageBox.critical(self, 'Ошибка сохранения', f'Не удалось сохранить журналиста в INI файл: {e}')
            return

        # Обновляем список в форме
        self.comboJournalists.clear()
        self.comboJournalists.addItems(names)
        self.comboJournalists.setCurrentText(new_name)

        self.labelStatus.setText(f"Журналист '{new_name}' добавлен в список.")

    # Метод select_directory_ingest удалён, т.к. путь инжеста из конфига


    def start_ingest(self):
    
        self.labelStatus.setStyleSheet("")  # Сброс стиля
    
        if self.ingest_root_path is None:
            QMessageBox.critical(self, 'Ошибка', 'Путь для инжеста не настроен в config.ini.')
            return

        if not hasattr(self, 'selected_directory') or not self.selected_directory:
            QMessageBox.warning(self, "Ошибка", "Сначала выберите директорию с файлами mts (нажмите 'Найти файлы MTS').")
            return

        # Получаем выбранные файлы в таблице
        selected_rows = set(index.row() for index in self.tableFiles.selectionModel().selectedRows())
        all_rows = range(self.tableFiles.rowCount())

        if selected_rows:
            rows_to_copy = selected_rows
        else:
            rows_to_copy = all_rows

        files_to_copy = []
        for row in rows_to_copy:
            item = self.tableFiles.item(row, 0)
            if item:
                files_to_copy.append(item.text())

        if not files_to_copy:
            QMessageBox.warning(self, 'Ошибка', 'Нет файлов для копирования.')
            return

        # Формируем имя папки: ДДММ Фамилия Название сюжета
        day_month = self.now.strftime('%d%m')

        journalist = self.comboJournalists.currentText().strip()
        story = self.lineEditStoryName.text().strip()

        if not journalist or not story:
            QMessageBox.warning(self, 'Ошибка', 'Введите ФИО журналиста и название сюжета.')
            return

        folder_name = f'{day_month} {journalist} {story}'
        
        # Используем путь из конфига
        dest_folder = os.path.join(self.ingest_root_path, folder_name)
        #self.buttonIngest.setEnabled(False)  # Блокируем кнопку перед кодированием (иначе форма зависнет)
        
        # Прячем кнопку инжеста с формы
        self.buttonIngest.hide()
        
        # Добавляем таймер и кнопку "Стоп" в форму
        self.h_layout2.addWidget(self.labelTimer)
        self.h_layout2.addWidget(self.buttonStop)
        
        for i in range(self.h_layout2.count()):
            w = self.h_layout2.itemAt(i).widget()
            if w is not None:
                w.show() # Показываем фиджеты в self.h_layout2 на форме
        
        #self.layout.insertLayout(4, self.h_layout2)
        # self.h_layout2 был ранее добавлен в основной layout в __init__
        
        # Запскаем таймер для копирования
        self.start_main_timer()

        # Запускаем копирование в отдельном потоке
        self.worker_copy = CopyFilesWorker(files_to_copy, self.selected_directory, dest_folder)
        self.worker_copy.finished.connect(lambda success, msg: self.on_copy_finished(success, msg, dest_folder, files_to_copy, story))
        self.worker_copy.start()
        self.labelStatus.setText("Копирование файлов...")

    def on_copy_finished(self, success, msg, dest_folder, files_to_copy, story):
    
        self.stop_main_timer() # Сбрасываем таймер для копирования
        self.start_main_timer() # Запускаем таймер для кодирования
    
        if not success:
            QMessageBox.critical(self, "Ошибка", msg)
            self.labelStatus.setStyleSheet("background-color: red; color: white;")
            self.labelStatus.setText(msg)
            self.buttonIngest.setEnabled(True)
            return

        self.labelStatus.setText("Копирование завершено, подготовка к кодированию...")

        # Создаём файл списка для ffmpeg (concat.txt в %TEMP% с путями)
        tmp_dir = tempfile.gettempdir()
        concat_file_path = os.path.join(tmp_dir, 'concat.txt')

        try:
            with open(concat_file_path, 'w', encoding='utf-8') as f:
                for filename in files_to_copy:
                    full_path = os.path.join(dest_folder, filename)
                    f.write(f"file '{full_path}'\n")
        except Exception as e:
            QMessageBox.critical(self, 'Ошибка', f'Не удалось создать concat.txt: {e}')
            return

        self.labelStatus.setText('Копирование и подготовка завершены. Готово.')
        QMessageBox.information(self, "Операция завершена", 
                                 f"Файлы скопированы в {dest_folder}\n"
                                 f"Создан 'concat.txt'. FFmpeg теперь может начать работу.")

        QApplication.processEvents()

        # Путь к ffmpeg (укажите реальный путь к ffmpeg.exe на вашем компьютере)
        ffmpeg_path = r'./ffmpeg/bin/ffmpeg.exe'

        # Путь к файлу concat.txt (список файлов для конкатенации)
        concat_file_path = os.path.join(tmp_dir, 'concat.txt')

        # Имя выходного файла (например, имя сюжета с расширением .mxf)
        output_filename = f'{story}.mxf'
        output_file_path = os.path.join(dest_folder, output_filename)
        target_path = os.path.join(self.mxf_target_folder, output_filename)

        # Формируем команду ffmpeg
        ffmpeg_cmd = [
            ffmpeg_path,
            "-y",
            "-safe", "0",
            "-f", "concat",
            "-i", concat_file_path,
            "-loglevel", "repeat+error",
            "-stats",
            "-filter_complex", "[v]format=yuv422p,scale=1920x1080;"
                               "[a]pan=1|c0=c0;"
                               "[a]pan=1|c0=c1;"
                               "[a]pan=1|c0=c2;"
                               "[a]pan=1|c0=c3;"
                               "[a]pan=1|c0=c4;"
                               "[a]pan=1|c0=c5;"
                               "[a]pan=1|c0=c6;"
                               "[a]pan=1|c0=c7",
            "-minrate", "50M",
            "-maxrate", "50M",
            "-dc", "10",
            "-intra_vlc", "1",
            "-non_linear_quant", "1",
            "-lmin", "1*QP2LAMBDA",
            "-rc_max_vbv_use", "1",
            "-rc_min_vbv_use", "1",
            "-qmin", "1",
            "-qmax", "12",
            "-vtag", "xd5e",
            "-vminrate", "50M",
            "-f", "mxf",
            "-c:a", "pcm_s24le",
            "-ac", "1",
            "-ar", "48000",
            "-ab", "384k",
            "-c:v", "mpeg2video",
            "-vb", "50M",
            "-vmaxrate", "50M",
            "-vbufsize", "36408360",
            "-g", "12",
            "-bf", "2",
            "-aspect", "1.77778",
            "-top", "1",
            "-alternate_scan", "1",
            "-r", "25",
            "-threads", "3",
            output_file_path
        ]

        self.labelStatus.setText('Кодирование...')
        QApplication.processEvents()

        # Запускаем кодирование в отдельном потоке
        self.worker = FFmpegWorker(ffmpeg_cmd, output_file_path, target_path)
        self.worker.finished.connect(self.on_encoding_finished)
        self.worker.start()


    def on_encoding_finished(self, success, message):

        # Остановка таймера
        self.stop_main_timer()
        
        # Прячем таймер и кнопку "Стоп" с формы
        for i in range(self.h_layout2.count()):
            w = self.h_layout2.itemAt(i).widget()
            if w is not None:
                w.hide() # Прячем фиджеты в self.h_layout2 на форме

        
        # Возвращаем кнопку инжеста на форму
        self.buttonIngest.show()

        #self.buttonIngest.setEnabled(True)    # разблокируем кнопку инжеста
        
        if success:
            self.labelStatus.setStyleSheet("background-color: green; color: white;")
        else:
            self.labelStatus.setStyleSheet("background-color: red; color: white;")
        self.labelStatus.setText(message)
        QMessageBox.information(self, "Результат", message)
        self.worker = None
    
    
    def start_main_timer(self, label_title=""):
        self.elapsed_seconds = 0
        self.labelTimer.setText("00:00:00")
        self.timer.start(1000)
        self.buttonStop.setEnabled(True)
        if label_title:
            self.labelStatus.setText(label_title)


    def stop_main_timer(self):
        self.timer.stop()
        self.labelTimer.setText("00:00:00")
        self.buttonStop.setEnabled(False)


    def update_timer(self):
        self.elapsed_seconds += 1
        h = self.elapsed_seconds // 3600
        m = (self.elapsed_seconds % 3600) // 60
        s = self.elapsed_seconds % 60
        self.labelTimer.setText(f"{h:02d}:{m:02d}:{s:02d}")
    
    
    def handle_stop_pressed(self):
        stopped = False

        if self.worker_copy and self.worker_copy.isRunning():
            self.worker_copy.terminate()
            self.worker_copy.wait(2000)
            self.worker_copy = None
            stopped = True

        if self.worker and self.worker.isRunning():
            self.worker.terminate_ffmpeg_process()
            self.worker.wait(5000)
            self.worker = None
            stopped = True

        self.stop_main_timer()
        self.buttonIngest.setEnabled(True)

        if stopped:
            self.labelStatus.setStyleSheet("background-color: red; color: white;")
            self.labelStatus.setText("Операция остановлена пользователем.")
        
        # Прячем таймер и кнопку "Стоп" с формы
        for i in range(self.h_layout2.count()):
            w = self.h_layout2.itemAt(i).widget()
            if w is not None:
                w.hide() # Прячем фиджеты в self.h_layout2 на форме
        
        # Возвращаем кнопку инжеста на форму
        self.buttonIngest.show()


    def closeEvent(self, event):
        '''
        Переопределяем метод закрытия окна для корректного завершения потоков.
        '''
        # Спрашиваем подтверждение закрытия, чтобы случайно не закрыть
        reply = QMessageBox.question(self, 'Выход',
                                     'Вы уверены, что хотите закрыть приложение? Незавершенные операции будут отменены.',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if reply == QMessageBox.Yes:

            # Завершаем поток копирования, если он активен
            if self.worker_copy and self.worker_copy.isRunning():
                print("Завершаем поток копирования...")
                self.worker_copy.terminate() # QThread.terminate()
                self.worker_copy.wait(2000) # Даем 2 секунды на завершение
                self.stop_main_timer()
                self.worker_copy = None

            # Завершаем поток ffmpeg, если он активен
            if self.worker and self.worker.isRunning():
                print("Завершаем поток ffmpeg...")
                # Вызываем новый метод для завершения процесса ffmpeg
                self.worker.terminate_ffmpeg_process()
                self.worker.wait(5000) # Ждем, пока поток завершится (до 5 секунд)
                self.stop_main_timer()
                self.worker = None

            event.accept() # Принимаем событие закрытия
        else:
            event.ignore() # Отклоняем событие закрытия


if __name__ == '__main__':
    app = QApplication(sys.argv)
    ingest_form = IngestFormMain()
    ingest_form.show()
    sys.exit(app.exec_())
